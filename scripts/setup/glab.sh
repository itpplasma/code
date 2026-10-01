#!/usr/bin/env bash
# Install the latest GitLab CLI release for this user and architecture.
set -euo pipefail

case "$(uname -m)" in
    x86_64) glab_arch=amd64 ;;
    aarch64|arm64) glab_arch=arm64 ;;
    *) echo "unsupported glab architecture: $(uname -m)" >&2; exit 1 ;;
esac

metadata="$(mktemp)"
archive="$(mktemp)"
stage="$(mktemp -d)"
trap 'rm -f "${metadata}" "${archive}"; rm -rf "${stage}"' EXIT

curl -fsSL \
    https://gitlab.com/api/v4/projects/gitlab-org%2Fcli/releases/permalink/latest \
    -o "${metadata}"
suffix="linux_${glab_arch}%2Etar%2Egz"
download_url="$(jq -r --arg suffix "${suffix}" \
    '.assets.links[].url | select(endswith($suffix))' "${metadata}" | head -n 1)"
[[ -n "${download_url}" ]] || { echo "could not find a glab release archive" >&2; exit 1; }

curl -fsSL "${download_url}" -o "${archive}"
tar -xzf "${archive}" -C "${stage}"
install -m 0755 "${stage}/bin/glab" "${HOME}/.local/bin/glab"
"${HOME}/.local/bin/glab" --version
