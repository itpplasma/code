#!/usr/bin/env bash
# Provision the Incus AI sandbox: btrfs pool, filtered bridge, profile,
# persistent home volume, and the Debian trixie container. Safe to re-run.
#
# The sandbox is one long-lived, unprivileged container. Host directories are
# attached per session by scripts/ai-sandbox.sh, not by this script.
set -euo pipefail

POOL="${AI_SANDBOX_POOL:-storage}"
POOL_SOURCE="${AI_SANDBOX_POOL_SOURCE:-/mnt/storage/incus}"
BRIDGE="${AI_SANDBOX_BRIDGE:-incusbr0}"
BRIDGE_CIDR="${AI_SANDBOX_BRIDGE_CIDR:-10.234.0.1/24}"
ACL="${AI_SANDBOX_ACL:-ai-sandbox-egress}"
PROFILE="${AI_SANDBOX_PROFILE:-ai-sandbox}"
VOLUME="${AI_SANDBOX_VOLUME:-ai-home}"
INSTANCE="${AI_SANDBOX_INSTANCE:-ai}"
IMAGE="${AI_SANDBOX_IMAGE:-images:debian/13}"
GUEST_USER="${AI_SANDBOX_USER:-${USER}}"
GUEST_UID="${AI_SANDBOX_UID:-$(id -u)}"
GUEST_HOME="/home/${GUEST_USER}"

log() { echo "[sandbox] $*"; }
err() { echo "[sandbox] ERROR: $*" >&2; }

command -v incus >/dev/null || { err "incus is not installed"; exit 1; }

# incus-admin membership grants host-root-equivalent control. Without it every
# call needs sudo, which this script tolerates but the launcher does not.
if ! incus info >/dev/null 2>&1; then
    err "cannot reach the incus daemon as $(id -un)"
    err "run: sudo usermod -aG incus-admin $(id -un)  then start a new login session"
    exit 1
fi

# The daemon needs a subuid/subgid range to build unprivileged containers.
for f in /etc/subuid /etc/subgid; do
    grep -q '^root:' "$f" || {
        err "$f has no root range; add: root:1000000:1000000000"
        exit 1
    }
done

exists() { "$@" >/dev/null 2>&1; }

if ! exists incus storage show "${POOL}"; then
    [[ -d "${POOL_SOURCE}" ]] || {
        log "Creating btrfs subvolume ${POOL_SOURCE}..."
        sudo btrfs subvolume create "${POOL_SOURCE}"
        sudo chmod 0711 "${POOL_SOURCE}"
    }
    log "Creating storage pool ${POOL} on ${POOL_SOURCE}..."
    incus storage create "${POOL}" btrfs source="${POOL_SOURCE}"
fi

if ! exists incus network show "${BRIDGE}"; then
    log "Creating bridge ${BRIDGE}..."
    incus network create "${BRIDGE}" \
        ipv4.address="${BRIDGE_CIDR}" ipv4.nat=true ipv6.address=none
fi

# Egress policy: public internet yes, private networks no. The allow rules for
# the bridge address must precede the 10/8 drop, because the gateway that
# serves DNS and DHCP lives inside that range.
if ! exists incus network acl show "${ACL}"; then
    log "Creating network ACL ${ACL}..."
    incus network acl create "${ACL}"
    gw="${BRIDGE_CIDR%/*}"
    incus network acl rule add "${ACL}" egress action=allow \
        destination="${gw}/32" protocol=udp destination_port=53
    incus network acl rule add "${ACL}" egress action=allow \
        destination="${gw}/32" protocol=tcp destination_port=53
    incus network acl rule add "${ACL}" egress action=allow \
        destination="${gw}/32" protocol=udp destination_port=67
    for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16; do
        incus network acl rule add "${ACL}" egress action=drop destination="${net}"
    done
fi

if ! exists incus profile show "${PROFILE}"; then
    log "Creating profile ${PROFILE}..."
    incus profile create "${PROFILE}"
    incus profile device add "${PROFILE}" eth0 nic \
        network="${BRIDGE}" name=eth0 \
        security.acls="${ACL}" \
        security.acls.default.egress.action=allow \
        security.acls.default.ingress.action=drop
    incus profile device add "${PROFILE}" root disk pool="${POOL}" path=/
fi

if ! exists incus storage volume show "${POOL}" "${VOLUME}"; then
    log "Creating home volume ${POOL}/${VOLUME}..."
    incus storage volume create "${POOL}" "${VOLUME}"
fi

if ! exists incus info "${INSTANCE}"; then
    log "Launching ${INSTANCE} from ${IMAGE}..."
    incus launch "${IMAGE}" "${INSTANCE}" --profile "${PROFILE}"
    incus exec "${INSTANCE}" -- cloud-init status --wait >/dev/null 2>&1 || true
fi

if ! incus config device show "${INSTANCE}" | grep -q '^  home:'; then
    log "Attaching ${VOLUME} at ${GUEST_HOME}..."
    incus config device add "${INSTANCE}" home disk \
        pool="${POOL}" source="${VOLUME}" path="${GUEST_HOME}"
fi

# The guest user shares the host uid so idmapped bind mounts keep ownership
# identical on both sides.
if ! incus exec "${INSTANCE}" -- id -u "${GUEST_USER}" >/dev/null 2>&1; then
    log "Creating guest user ${GUEST_USER} (uid ${GUEST_UID})..."
    incus exec "${INSTANCE}" -- useradd \
        --uid "${GUEST_UID}" --shell /bin/bash --home-dir "${GUEST_HOME}" "${GUEST_USER}"
fi
incus exec "${INSTANCE}" -- install -d \
    -o "${GUEST_USER}" -g "${GUEST_USER}" -m 0755 "${GUEST_HOME}"
incus exec "${INSTANCE}" -- bash -c "
    for f in .bashrc .profile; do
        [[ -e ${GUEST_HOME}/\$f ]] || cp /etc/skel/\$f ${GUEST_HOME}/\$f
        chown ${GUEST_USER}:${GUEST_USER} ${GUEST_HOME}/\$f
    done"

log "Ready. Start a session with: scripts/ai-sandbox.sh"
