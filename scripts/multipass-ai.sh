#!/usr/bin/env bash
# Launch a reproducible Ubuntu LTS AI coding VM without host-directory mounts.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
NAME="${MULTIPASS_AI_NAME:-ai}"
IMAGE="${MULTIPASS_AI_IMAGE:-26.04}"
CPUS="${MULTIPASS_AI_CPUS:-8}"
MEMORY="${MULTIPASS_AI_MEMORY:-16G}"
DISK="${MULTIPASS_AI_DISK:-128G}"
TIMEOUT="${MULTIPASS_AI_TIMEOUT:-3600}"

usage() {
    cat <<EOF
Usage: $0 [--name NAME] [--image IMAGE] [--cpus N] [--memory SIZE] [--disk SIZE]

Defaults: name=${NAME}, image=${IMAGE}, cpus=${CPUS}, memory=${MEMORY}, disk=${DISK}
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name) NAME="${2:?--name requires a value}"; shift 2 ;;
        --image) IMAGE="${2:?--image requires a value}"; shift 2 ;;
        --cpus) CPUS="${2:?--cpus requires a value}"; shift 2 ;;
        --memory) MEMORY="${2:?--memory requires a value}"; shift 2 ;;
        --disk) DISK="${2:?--disk requires a value}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ "${NAME}" != "primary" ]] || {
    echo "refusing the name 'primary': Multipass may auto-mount the host home there" >&2
    exit 1
}
command -v multipass >/dev/null || {
    echo "Multipass is required: https://multipass.run/install" >&2
    exit 1
}
if multipass info "${NAME}" >/dev/null 2>&1; then
    echo "Multipass instance already exists: ${NAME}" >&2
    exit 1
fi

multipass launch "${IMAGE}" \
    --name "${NAME}" \
    --cpus "${CPUS}" \
    --memory "${MEMORY}" \
    --disk "${DISK}" \
    --timeout "${TIMEOUT}" \
    --cloud-init "${REPO_ROOT}/cloud-init/multipass-ai.yaml"

multipass exec "${NAME}" -- cloud-init status --wait --long
multipass exec "${NAME}" -- bash -lc \
    'cat /etc/ai-infra-release; printf "disk: "; findmnt -no SIZE /; command -v codex claude opencode pi uv gh go'

cat <<EOF

Ready: multipass shell ${NAME}
Inside the VM, authenticate with gh/claude/codex/opencode, then run:
  ai-private-tools
EOF
