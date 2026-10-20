#!/usr/bin/env bash
# Clone/update private sloppy-org tools after the user has authenticated gh.
set -euo pipefail

export PATH="${HOME}/.local/bin:${HOME}/.opencode/bin:${HOME}/go/bin:${PATH}"
SOURCE_ROOT="${AI_SOURCE_ROOT:-${HOME}/src}"
PROJECT_DIR="${SLOPTOOLS_PROJECT_DIR:-${HOME}/workspace}"
DATA_DIR="${SLOPTOOLS_DATA_DIR:-${HOME}/.local/share/sloppy}"
repos=(helpy sloptools)

command -v gh >/dev/null || { echo "gh is not installed" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || {
    echo "Authenticate first with: gh auth login" >&2
    exit 1
}
command -v go >/dev/null || { echo "Go is not installed" >&2; exit 1; }

mkdir -p "${SOURCE_ROOT}/sloppy-org" "${HOME}/go/bin" "${PROJECT_DIR}" "${DATA_DIR}"
gh auth setup-git

for repo in "${repos[@]}"; do
    dest="${SOURCE_ROOT}/sloppy-org/${repo}"
    if [[ -d "${dest}/.git" ]]; then
        git -C "${dest}" pull --ff-only
    else
        gh repo clone "sloppy-org/${repo}" "${dest}"
    fi
    (cd "${dest}" && go install "./cmd/${repo}")
done

export HELPY_BIN="${HOME}/go/bin/helpy"
export SLOPTOOLS_BIN="${HOME}/go/bin/sloptools"
export SLOPTOOLS_PROJECT_DIR="${PROJECT_DIR}"
export SLOPTOOLS_DATA_DIR="${DATA_DIR}"
bash "${SOURCE_ROOT}/sloppy-org/helpy/scripts/setup-helpy-mcp.sh"
bash "${SOURCE_ROOT}/sloppy-org/sloptools/scripts/setup-sloptools-mcp.sh"

echo "Private tools updated; MCP access is restricted to ${PROJECT_DIR}."
