#!/usr/bin/env bash
set -euo pipefail

# Fetch selected subtrees of the shared ITPcp plasma data repository
# (gitlab.tugraz.at/plasma/data) into $DATA so the integration tests in
# tests/ can run against real experimental and design equilibria.
#
# Authentication is via a read-only GitLab token in GITLAB_ACCESS_TOKEN.
# Without a token the script exits 0 and tests relying on $DATA skip.
#
# Usage: scripts/fetch_data.sh <subdir> [<subdir> ...]
# Example: scripts/fetch_data.sh AUG/EQDSK AUG/BOOZER/30835

if [[ -z "${GITLAB_ACCESS_TOKEN:-}" ]]; then
    echo "GITLAB_ACCESS_TOKEN is not set; skipping DATA fetch"
    exit 0
fi

if [[ $# -eq 0 ]]; then
    echo "usage: $0 <subdir> [<subdir> ...]" >&2
    exit 2
fi

repo_url="${GITLAB_DATA_REPO_URL:-https://oauth2:${GITLAB_ACCESS_TOKEN}@gitlab.tugraz.at/plasma/data.git}"
data_root="${DATA:-$(pwd)/.testdata}"
branch="${GITHUB_HEAD_REF:-${GITHUB_REF_NAME:-main}}"

# Prefer a sparse, blob-free clone so we only pull the requested subtrees
# (LFS blobs are filtered further below). Full clones of plasma/data are slow.
tmpdir="$(mktemp -d)"
cleanup() {
    rm -rf "$tmpdir"
}
trap cleanup EXIT

echo "Fetching DATA subtrees ($*) on branch ${branch}"
git clone --filter=blob:none --sparse --no-checkout "$repo_url" "$tmpdir/data"
cd "$tmpdir/data"

if git ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
    git checkout "$branch" 2>/dev/null || true
else
    echo "Branch ${branch} not found in data repo; falling back to main"
    git checkout main 2>/dev/null || true
fi

git sparse-checkout set "$@"

if [[ -f .gitattributes ]] && grep -q 'filter=lfs' .gitattributes; then
    git lfs install --local >/dev/null 2>&1 || true
    git config lfs.fetchinclude "$(IFS=,; echo "$*")"
    git config lfs.fetchexclude ""
    git lfs pull 2>/dev/null || true
    git lfs checkout "$@" 2>/dev/null || true
fi

# Only copy the requested subtrees into $DATA.
mkdir -p "$data_root"
for subdir in "$@"; do
    if [[ ! -d "$subdir" ]]; then
        echo "Subtree ${subdir} not found in data repo; skipping"
        continue
    fi
    mkdir -p "$data_root/$(dirname "$subdir")"
    cp -a "$subdir" "$data_root/$subdir"
done
echo "DATA subtrees synchronized to $data_root"
