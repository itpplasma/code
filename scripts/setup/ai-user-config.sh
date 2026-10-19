#!/usr/bin/env bash
# Apply a user-selected chezmoi repository after service authentication.
set -euo pipefail

export PATH="${HOME}/.local/bin:${PATH}"
repo="${1:-${CHEZMOI_REPO:-}}"

command -v chezmoi >/dev/null || { echo "chezmoi is not installed" >&2; exit 1; }
if [[ -n "${repo}" ]]; then
    chezmoi init --apply "${repo}"
elif [[ -d "${HOME}/.local/share/chezmoi/.git" ]]; then
    chezmoi update
else
    echo "usage: ai-user-config GIT_REPOSITORY" >&2
    echo "The repository may contain age-encrypted files; no repository is baked into the VM." >&2
    exit 2
fi
