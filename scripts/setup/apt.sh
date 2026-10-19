#!/usr/bin/env bash
# Set up a Debian or Ubuntu machine using shared apt package components.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="full"
TARGET_USER="${SUDO_USER:-}"
DO_UPGRADE=1

usage() {
    cat <<'EOF'
Usage: apt.sh [full|ai] [--user USER] [--no-upgrade]

  full  Existing Debian scientific workstation, including TeX and Octave.
  ai    Headless AI/scientific coding VM; no desktop, TeX, or Octave stack.
EOF
}

if [[ $# -gt 0 && "$1" != --* ]]; then
    PROFILE="$1"
    shift
fi
while [[ $# -gt 0 ]]; do
    case "$1" in
        --user) TARGET_USER="${2:?--user requires a value}"; shift 2 ;;
        --no-upgrade) DO_UPGRADE=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ "${PROFILE}" == "full" || "${PROFILE}" == "ai" ]] || {
    echo "unsupported profile: ${PROFILE}" >&2
    exit 2
}
[[ ${EUID} -eq 0 ]] || {
    echo "run with sudo: sudo $0 ${PROFILE}" >&2
    exit 1
}

# shellcheck disable=SC1091
source /etc/os-release
case "${ID}" in
    debian|ubuntu) ;;
    *) echo "this installer supports Debian and Ubuntu, not ${ID}" >&2; exit 1 ;;
esac

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
apt-get update -y
if [[ ${DO_UPGRADE} -eq 1 ]]; then
    apt-get upgrade -y -q --no-install-recommends
fi

"${SCRIPT_DIR}/debian/base.sh"
"${SCRIPT_DIR}/debian/libs.sh"

if [[ "${PROFILE}" == "full" ]]; then
    "${SCRIPT_DIR}/debian/interactive.sh"
    "${SCRIPT_DIR}/debian/octave.sh"
    "${SCRIPT_DIR}/debian/texlive.sh"
    "${SCRIPT_DIR}/debian/fonts.sh"
    exit 0
fi

"${SCRIPT_DIR}/debian/ai.sh"

if [[ -z "${TARGET_USER}" ]]; then
    TARGET_USER="$(awk -F: '$3 >= 1000 && $3 < 60000 && $7 !~ /(nologin|false)$/ {print $1; exit}' /etc/passwd)"
fi
[[ -n "${TARGET_USER}" ]] || {
    echo "could not determine the interactive user; pass --user USER" >&2
    exit 1
}
TARGET_HOME="$(getent passwd "${TARGET_USER}" | cut -d: -f6)"
TARGET_GROUP="$(id -gn "${TARGET_USER}")"
[[ -d "${TARGET_HOME}" ]] || {
    echo "home directory not found for ${TARGET_USER}" >&2
    exit 1
}

install -d -o "${TARGET_USER}" -g "${TARGET_GROUP}" "${TARGET_HOME}/workspace"
runuser -u "${TARGET_USER}" -- env HOME="${TARGET_HOME}" USER="${TARGET_USER}" \
    bash "${SCRIPT_DIR}/ai-tools.sh"

echo "AI profile installed for ${TARGET_USER}."
