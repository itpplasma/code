#!/usr/bin/env bash
# Install the current stable Go toolchain for this user and architecture.
set -euo pipefail

case "$(uname -m)" in
    x86_64) go_arch=amd64 ;;
    aarch64|arm64) go_arch=arm64 ;;
    *) echo "unsupported Go architecture: $(uname -m)" >&2; exit 1 ;;
esac

go_version="${GO_VERSION:-$(curl -fsSL 'https://go.dev/VERSION?m=text' | head -n 1)}"
install_root="${HOME}/.local/share/go/${go_version}"
if [[ ! -x "${install_root}/bin/go" ]]; then
    archive="$(mktemp)"
    stage="$(mktemp -d)"
    trap 'rm -f "${archive}"; rm -rf "${stage}"' EXIT
    curl -fsSL "https://go.dev/dl/${go_version}.linux-${go_arch}.tar.gz" -o "${archive}"
    tar -xzf "${archive}" -C "${stage}"
    mkdir -p "$(dirname "${install_root}")"
    mv "${stage}/go" "${install_root}"
fi
ln -sfn "${install_root}" "${HOME}/.local/go"
"${HOME}/.local/go/bin/go" version
