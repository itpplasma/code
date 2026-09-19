#!/usr/bin/env bash
# Create the local/cloud Incus instances used by the ai dispatcher.
#
# The existing `ai` instance is the source and is never modified or stopped.
# Each target gets its own persistent home volume and a read-only prompts
# checkout.  Workspace directories remain session mounts managed by
# scripts/ai-sandbox.sh.  This script intentionally does not install, remove,
# or register MCP services.
set -euo pipefail

BASE="${AI_SANDBOX_BASE_INSTANCE:-ai}"
POOL="${AI_SANDBOX_POOL:-storage}"
LOCAL="${AI_SANDBOX_LOCAL_INSTANCE:-ai-local}"
CLOUD="${AI_SANDBOX_CLOUD_INSTANCE:-ai-cloud}"
GUEST_USER="${AI_SANDBOX_USER:-${USER}}"
GUEST_HOME="/home/${GUEST_USER}"
PROMPTS_SOURCE="${AI_PROMPTS_SOURCE:-${HOME:?HOME is not set}/code/prompts}"
PROMPTS_PATH="${AI_PROMPTS_PATH:-${GUEST_HOME}/prompts}"
LLAMA_PORT="${AI_LLAMA_PORT:-8080}"
AI_BRIDGE="${AI_SANDBOX_PROFILES_BRIDGE:-ai0}"
AI_BRIDGE_CIDR="${AI_SANDBOX_PROFILES_BRIDGE_CIDR:-10.254.77.1/24}"
EGRESS_PROXY_IP="${AI_EGRESS_PROXY_IP:-10.254.77.1}"
EGRESS_PROXY_PORT="${AI_EGRESS_PROXY_PORT:-3128}"
LOCAL_EGRESS_PROXY_PORT="${AI_EGRESS_LOCAL_PROXY_PORT:-3129}"
LOCAL_IP="${AI_EGRESS_LOCAL_IP:-10.254.77.110}"
CLOUD_IP="${AI_EGRESS_CLOUD_IP:-10.254.77.12}"

log() { echo "[sandbox] $*"; }
err() { echo "[sandbox] ERROR: $*" >&2; }
exists() { "$@" >/dev/null 2>&1; }

command -v incus >/dev/null || { err "incus is not installed"; exit 1; }
exists incus info || { err "cannot reach the Incus daemon"; exit 1; }
exists incus info "${BASE}" || {
    err "source instance '${BASE}' does not exist; run scripts/setup/incus-ai-sandbox.sh first"
    exit 1
}
[[ -d "${PROMPTS_SOURCE}" ]] || {
    err "prompts checkout does not exist: ${PROMPTS_SOURCE}"
    exit 1
}

if ! exists incus network show "${AI_BRIDGE}"; then
    log "creating profile bridge ${AI_BRIDGE}"
    incus network create "${AI_BRIDGE}" \
        ipv4.address="${AI_BRIDGE_CIDR}" ipv4.nat=true ipv6.address=none
fi

ensure_volume() {
    local volume="$1"
    if ! exists incus storage volume show "${POOL}" "${volume}"; then
        log "creating ${POOL}/${volume}"
        incus storage volume create "${POOL}" "${volume}"
    fi
}

ensure_target() {
    local instance="$1" volume="$2" address="$3" proxy_port="$4"

    if ! exists incus info "${instance}"; then
        log "copying ${BASE} to ${instance}"
        # --stateless permits copying a currently running source without
        # copying runtime state.  The source remains running and untouched.
        incus copy "${BASE}" "${instance}" --instance-only --stateless
    fi

    ensure_volume "${volume}"

    # A copied instance inherits the source's home device.  Remove it before
    # attaching the private volume; never let local and cloud share a home.
    current_home_source=""
    current_home_path=""
    if exists incus config device get "${instance}" home source; then
        current_home_source="$(incus config device get "${instance}" home source)"
        current_home_path="$(incus config device get "${instance}" home path)"
    fi
    if [[ "${current_home_source}" != "${volume}" ||
          "${current_home_path}" != "${GUEST_HOME}" ]]; then
        # A copied instance inherits the source's home device.  Remove it
        # before attaching the private volume; never let local and cloud
        # share a home.
        if [[ -n "${current_home_source}" ]]; then
            incus config device remove "${instance}" home
        fi
        incus config device add "${instance}" home disk \
            pool="${POOL}" source="${volume}" path="${GUEST_HOME}" >/dev/null
    fi

    # Keep profile instances off the legacy ai/incusbr0 bridge. The dedicated
    # bridge lets the strict policy apply only to the new trust domains.
    current_network="$(incus config device get "${instance}" eth0 network 2>/dev/null || true)"
    if [[ "${current_network}" != "${AI_BRIDGE}" ]]; then
        incus config device override "${instance}" eth0 network="${AI_BRIDGE}" >/dev/null
    fi
    incus config device set "${instance}" eth0 ipv4.address="${address}"

    # `incus exec` below needs a running instance.  Copies made with
    # --stateless are stopped, so start before preparing the guest home and
    # prompts mount.  Starting here is safe for an already-running target.
    if [[ "$(incus info "${instance}" | awk '/^Status:/ {print tolower($2)}')" != running ]]; then
        log "starting ${instance} for guest setup"
        incus start "${instance}"
    fi

    if ! incus exec "${instance}" -- id -u "${GUEST_USER}" >/dev/null 2>&1; then
        local uid
        uid="${AI_SANDBOX_UID:-$(id -u)}"
        incus exec "${instance}" -- useradd \
            --uid "${uid}" --shell /bin/bash --home-dir "${GUEST_HOME}" "${GUEST_USER}"
    fi
    incus exec "${instance}" -- install -d \
        -o "${GUEST_USER}" -g "${GUEST_USER}" -m 0755 "${GUEST_HOME}"
    incus exec "${instance}" -- mkdir -p "${PROMPTS_PATH}"

    if exists incus config device show "${instance}"; then
        incus config device remove "${instance}" prompts >/dev/null 2>&1 || true
    fi
    incus config device add "${instance}" prompts disk \
        source="${PROMPTS_SOURCE}" path="${PROMPTS_PATH}" \
        readonly=true shift=true >/dev/null

    # A proxy device, when supported by the installed Incus version, exposes
    # only the host llama endpoint inside the instance.  Failure is non-fatal:
    # callers can use a separately configured local endpoint instead.
    incus config device remove "${instance}" llama-proxy >/dev/null 2>&1 || true
    if ! incus config device add "${instance}" llama-proxy proxy \
        bind=container listen="tcp:127.0.0.1:${LLAMA_PORT}" \
        connect="tcp:127.0.0.1:${LLAMA_PORT}" >/dev/null 2>&1; then
        log "llama proxy unavailable for ${instance}; leaving host services unchanged"
    fi

    # Terminate the guest-side proxy on loopback. This avoids exposing the
    # host bridge address to the guest and also survives Incus' bridge-level
    # RFC1918 filtering; the host-side Squid listener remains scoped to the
    # Incus bridge and the nft policy permits only that path.
    incus config device remove "${instance}" egress-proxy >/dev/null 2>&1 || true
    if ! incus config device add "${instance}" egress-proxy proxy \
        bind=container listen="tcp:127.0.0.1:${proxy_port}" \
        connect="tcp:${EGRESS_PROXY_IP}:${proxy_port}" >/dev/null 2>&1; then
        log "egress proxy unavailable for ${instance}; leaving host services unchanged"
    fi

}

ensure_target "${LOCAL}" "ai-local-home" "${LOCAL_IP}" "${LOCAL_EGRESS_PROXY_PORT}"
ensure_target "${CLOUD}" "ai-cloud-home" "${CLOUD_IP}" "${EGRESS_PROXY_PORT}"
log "ready: ${LOCAL} and ${CLOUD} (source ${BASE} was not modified)"
