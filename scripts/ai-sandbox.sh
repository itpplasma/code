#!/usr/bin/env bash
# Run a shell or a command inside the Incus AI sandbox, with the current
# working directory attached at the same absolute path it has on the host.
#
# The shared workspace roots are attached for every session, so all code and
# project checkouts remain visible regardless of the directory the launcher is
# started from. Other directories are attached per session and reference-
# counted: their bind mount disappears when the last session using it exits.
set -euo pipefail

INSTANCE="${AI_SANDBOX_INSTANCE:-ai}"
GUEST_USER="${AI_SANDBOX_USER:-${USER}}"
GUEST_HOME="/home/${GUEST_USER}"
HOST_HOME="${HOME:?HOME is not set}"
ALWAYS_MOUNT=(
    "${HOST_HOME}/code"
    "${HOST_HOME}/proj"
)
STATE_ROOT="${XDG_RUNTIME_DIR:-/tmp}/ai-sandbox"

# Keep the historical state directory for the legacy instance, while giving
# the two profile instances independent device/refcount state.  Otherwise a
# local and cloud session attaching the same path would race on one state
# file even though they are different Incus instances.
STATE_DIR="${STATE_ROOT}"
AS_ROOT=0

log() { echo "[sandbox] $*" >&2; }
err() { echo "[sandbox] ERROR: $*" >&2; }

usage() {
    cat <<EOF
Usage: $(basename "$0") [local|--local|cloud|--cloud] [command ...]
       $(basename "$0") --root [local|--local|cloud|--cloud] [command ...]
       $(basename "$0") status | detach [DIR] | prune | stop

With no command, opens an interactive login shell in the current directory.
Any command is run in the current directory as ${GUEST_USER}. ${HOST_HOME}/code
and ${HOST_HOME}/proj are always visible in the sandbox; the current directory
is also attached when it is outside those trees.

  local,--local  use ai-local when provisioned (the default)
  cloud,--cloud  use ai-cloud
  --root      run as root in the selected sandbox instead of ${GUEST_USER}
  status      list attached directories and live sessions
  detach DIR  force-detach DIR (default: the current directory)
  prune       detach every directory with no live session
  stop        stop the sandbox container
EOF
}

# Select a trust domain before command handling.  The default remains the
# original `ai` instance until ai-local has been provisioned, so upgrading the
# launcher never strands an existing installation.
while [[ $# -gt 0 ]]; do
    case "$1" in
        --root)
            AS_ROOT=1
            shift
            ;;
        local|--local)
            INSTANCE="${AI_SANDBOX_LOCAL_INSTANCE:-ai-local}"
            shift
            ;;
        cloud|--cloud)
            INSTANCE="${AI_SANDBOX_CLOUD_INSTANCE:-ai-cloud}"
            shift
            ;;
        *)
            break
            ;;
    esac
done

if [[ "${INSTANCE}" == "ai" && -z "${AI_SANDBOX_INSTANCE+x}" ]] &&
   command -v incus >/dev/null 2>&1 &&
   incus info "${AI_SANDBOX_LOCAL_INSTANCE:-ai-local}" >/dev/null 2>&1; then
    INSTANCE="${AI_SANDBOX_LOCAL_INSTANCE:-ai-local}"
fi

if [[ "${INSTANCE}" != ai && "${INSTANCE}" != ai-local && "${INSTANCE}" != ai-cloud ]]; then
    # Explicit AI_SANDBOX_INSTANCE remains useful for development/test copies.
    STATE_DIR="${STATE_ROOT}/${INSTANCE}"
elif [[ "${INSTANCE}" != ai ]]; then
    STATE_DIR="${STATE_ROOT}/${INSTANCE}"
fi

# Incus device names allow no slashes, so derive a stable one from the path.
device_name() {
    printf 'work-%s' "$(printf '%s' "$1" | sha256sum | cut -c1-12)"
}

require_instance() {
    # A permission error looks identical to a missing instance unless the
    # daemon is probed separately. Being added to incus-admin does not affect
    # shells that were already open.
    incus info >/dev/null 2>&1 || {
        err "cannot reach the incus daemon as $(id -un)"
        if id -nG | tr ' ' '\n' | grep -qx incus-admin; then
            err "is incus running? try: systemctl status incus"
        else
            err "your session is not in the incus-admin group; start a new login"
            err "session, or for this shell: newgrp incus-admin"
        fi
        exit 1
    }
    incus info "${INSTANCE}" >/dev/null 2>&1 || {
        err "sandbox '${INSTANCE}' does not exist; run scripts/setup/incus-ai-sandbox.sh"
        exit 1
    }
    if [[ "$(incus info "${INSTANCE}" | awk '/^Status:/ {print tolower($2)}')" != running ]]; then
        log "starting ${INSTANCE}..."
        # Another caller can start the instance after the status check but
        # before this start call. Treat that expected race as success.
        local start_error
        if ! start_error="$(incus start "${INSTANCE}" 2>&1)"; then
            if [[ "$(incus info "${INSTANCE}" | awk '/^Status:/ {print tolower($2)}')" != running ]]; then
                err "failed to start ${INSTANCE}: ${start_error}"
                exit 1
            fi
        fi
        local ready=0
        for _ in $(seq 30); do
            if incus exec "${INSTANCE}" -- true >/dev/null 2>&1; then
                ready=1
                break
            fi
            sleep 0.2
        done
        if (( ! ready )); then
            err "${INSTANCE} did not become ready after starting"
            exit 1
        fi
    fi
}

# A directory that would shadow the sandbox's own home breaks the persistent
# home volume, and attaching a whole tree defeats the point of per-directory
# access.
reject_unsafe() {
    local dir="$1"
    case "$dir" in
        / | /home | "${GUEST_HOME}" | /mnt | /mnt/storage)
            err "refusing to attach ${dir}: it would shadow the sandbox home or expose a whole tree"
            exit 1
            ;;
    esac
}

# Adding a device rewrites the whole instance config, so two concurrent adds
# collide on the API ETag and one of them loses. The lock is therefore
# instance-wide, not per directory; it is held only for the config call, never
# for the session itself.
with_lock() {
    mkdir -p "${STATE_DIR}"
    exec 9>"${STATE_DIR}/instance.lock"
    flock 9
    "$@"
    local rc=$?
    flock -u 9
    exec 9>&-
    return ${rc}
}

_attach() {
    local dir="$1" dev
    dev="$(device_name "${dir}")"
    mkdir -p "${STATE_DIR}/${dev}.sessions"
    if ! incus config device get "${INSTANCE}" "${dev}" source >/dev/null 2>&1; then
        incus exec "${INSTANCE}" -- mkdir -p "${dir}"
        incus config device add "${INSTANCE}" "${dev}" disk \
            source="${dir}" path="${dir}" shift=true >/dev/null
    fi
    printf '%s\n' "${dir}" > "${STATE_DIR}/${dev}.path"
    : > "${STATE_DIR}/${dev}.sessions/$$"
}

attach() { with_lock _attach "$1"; }

is_within() {
    local path="$1" root="$2"
    [[ "${path}" == "${root}" || "${path}" == "${root}"/* ]]
}

_detach_if_idle() {
    local dir="$1" dev
    dev="$(device_name "${dir}")"
    rm -f "${STATE_DIR}/${dev}.sessions/$$"
    # Sessions killed without running their trap leave a stale marker behind.
    for pid in "${STATE_DIR}/${dev}.sessions"/*; do
        [[ -e "${pid}" ]] || continue
        kill -0 "$(basename "${pid}")" 2>/dev/null || rm -f "${pid}"
    done
    if [[ -z "$(ls -A "${STATE_DIR}/${dev}.sessions" 2>/dev/null)" ]]; then
        incus config device remove "${INSTANCE}" "${dev}" >/dev/null 2>&1 || true
        remove_mountpoint "${dir}"
        rm -rf "${STATE_DIR}/${dev}.sessions" "${STATE_DIR}/${dev}.path"
    fi
}

# Without this the sandbox keeps an empty directory skeleton of every path ever
# attached. rmdir only ever removes empty directories, so a path that holds
# real container content stops the walk.
remove_mountpoint() {
    incus exec "${INSTANCE}" -- bash -c '
        d="$1"; home="$2"
        while [ "$d" != "/" ] && [ "$d" != "$home" ]; do
            rmdir "$d" 2>/dev/null || break
            d="$(dirname "$d")"
        done' _ "$1" "${GUEST_HOME}" >/dev/null 2>&1 || true
}

detach_if_idle() { with_lock _detach_if_idle "$1"; }

cmd_status() {
    require_instance
    echo "sandbox: ${INSTANCE} ($(incus info "${INSTANCE}" | awk '/^Status:/ {print $2}'))"
    echo
    printf '%-12s %-8s %s\n' DEVICE SESSIONS DIRECTORY
    local any=0
    for pathfile in "${STATE_DIR}"/*.path; do
        [[ -e "${pathfile}" ]] || continue
        local dev n
        dev="$(basename "${pathfile}" .path)"
        n="$(ls -A "${STATE_DIR}/${dev}.sessions" 2>/dev/null | wc -l)"
        printf '%-12s %-8s %s\n' "${dev#work-}" "${n}" "$(cat "${pathfile}")"
        any=1
    done
    [[ ${any} -eq 1 ]] || echo "(no directories attached)"
}

cmd_prune() {
    require_instance
    for pathfile in "${STATE_DIR}"/*.path; do
        [[ -e "${pathfile}" ]] || continue
        local dev
        dev="$(basename "${pathfile}" .path)"
        for pid in "${STATE_DIR}/${dev}.sessions"/*; do
            [[ -e "${pid}" ]] || continue
            kill -0 "$(basename "${pid}")" 2>/dev/null || rm -f "${pid}"
        done
        if [[ -z "$(ls -A "${STATE_DIR}/${dev}.sessions" 2>/dev/null)" ]]; then
            log "detaching $(cat "${pathfile}")"
            incus config device remove "${INSTANCE}" "${dev}" >/dev/null 2>&1 || true
            remove_mountpoint "$(cat "${pathfile}")"
            rm -rf "${STATE_DIR}/${dev}.sessions" "${STATE_DIR}/${dev}.path"
        fi
    done
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    status) cmd_status; exit 0 ;;
    prune) cmd_prune; exit 0 ;;
    stop) incus stop "${INSTANCE}"; exit 0 ;;
    detach)
        require_instance
        target="${2:-$PWD}"
        dev="$(device_name "${target}")"
        incus config device remove "${INSTANCE}" "${dev}" >/dev/null 2>&1 || true
        rm -rf "${STATE_DIR}/${dev}.sessions" "${STATE_DIR}/${dev}.path"
        log "detached ${target}"
        exit 0
        ;;
esac

WORKDIR="$(pwd -P)"
reject_unsafe "${WORKDIR}"
require_instance

ATTACHED_DIRS=()
cleanup() {
    local i
    for ((i=${#ATTACHED_DIRS[@]} - 1; i >= 0; i--)); do
        detach_if_idle "${ATTACHED_DIRS[i]}"
    done
}
trap cleanup EXIT

for dir in "${ALWAYS_MOUNT[@]}"; do
    [[ -d "${dir}" ]] || {
        err "required workspace directory does not exist: ${dir}"
        exit 1
    }
    reject_unsafe "${dir}"
    attach "${dir}"
    ATTACHED_DIRS+=("${dir}")
done

if ! is_within "${WORKDIR}" "${ALWAYS_MOUNT[0]}" &&
   ! is_within "${WORKDIR}" "${ALWAYS_MOUNT[1]}"; then
    attach "${WORKDIR}"
    ATTACHED_DIRS+=("${WORKDIR}")
fi

exec_args=(exec "${INSTANCE}" --cwd "${WORKDIR}" --env "HOME=${GUEST_HOME}")
if [[ ${AS_ROOT} -eq 0 ]]; then
    uid="$(incus exec "${INSTANCE}" -- id -u "${GUEST_USER}")"
    gid="$(incus exec "${INSTANCE}" -- id -g "${GUEST_USER}")"
    exec_args+=(--user "${uid}" --group "${gid}" --env "USER=${GUEST_USER}")
fi

if [[ $# -gt 0 ]]; then
    # These aliases make the safety posture of the cloud coding entry point
    # explicit without changing how the underlying tools are installed or
    # configured.  Other tools receive exactly the arguments supplied by the
    # caller.
    case "$1" in
        claude) set -- claude --dangerously-skip-permissions "${@:2}" ;;
        codex)  set -- codex --yolo --search "${@:2}" ;;
    esac
fi

if [[ $# -eq 0 ]]; then
    incus "${exec_args[@]}" -- bash -l
else
    incus "${exec_args[@]}" -- "$@"
fi
