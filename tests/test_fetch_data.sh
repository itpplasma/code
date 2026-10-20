#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=Test GIT_AUTHOR_EMAIL=test@example.invalid
export GIT_COMMITTER_NAME=Test GIT_COMMITTER_EMAIL=test@example.invalid
unset GITHUB_HEAD_REF GITHUB_REF_NAME DATA_BRANCH

# No credentials must skip before attempting a network operation.
env -u GITLAB_ACCESS_TOKEN "$repo_root/scripts/fetch_data.sh" > "$scratch/skip.log"
grep -q 'skipping DATA fetch' "$scratch/skip.log"
export GITLAB_ACCESS_TOKEN=test-token
if "$repo_root/scripts/fetch_data.sh" > "$scratch/usage.log" 2>&1; then
    echo 'Expected missing-subtree usage failure' >&2; exit 1
fi
for subtree in /absolute ../outside AUG/../outside; do
    if "$repo_root/scripts/fetch_data.sh" "$subtree" > "$scratch/path.log" 2>&1; then
        echo 'Expected invalid-subtree failure' >&2; exit 1
    fi
done

git init -q -b main "$scratch/source"
mkdir -p "$scratch/source/AUG/EQDSK" "$scratch/source/DEMO/EQDSK"
printf 'equilibrium\n' > "$scratch/source/AUG/EQDSK/reference"
printf 'not requested\n' > "$scratch/source/DEMO/EQDSK/other"
git -C "$scratch/source" add AUG DEMO
git -C "$scratch/source" commit -qm 'Reference data'
export GITLAB_DATA_REPO_URL="$scratch/source"
export DATA="$scratch/output"
export GITHUB_HEAD_REF=missing-code-branch
"$repo_root/scripts/fetch_data.sh" AUG/EQDSK MISSING > "$scratch/fetch.log" 2>&1
cmp "$scratch/source/AUG/EQDSK/reference" "$DATA/AUG/EQDSK/reference"
[[ ! -e "$DATA/DEMO" ]]
grep -q 'falling back to main' "$scratch/fetch.log"
grep -q 'Subtree MISSING not found' "$scratch/fetch.log"
# Re-fetch into an existing relative destination must replace, without nesting.
printf 'updated\n' > "$scratch/source/AUG/EQDSK/reference"
git -C "$scratch/source" commit -qam 'Update reference'
(cd "$scratch"; DATA=output "$repo_root/scripts/fetch_data.sh" AUG/EQDSK > repeat.log 2>&1)
cmp "$scratch/source/AUG/EQDSK/reference" "$DATA/AUG/EQDSK/reference"
[[ ! -e "$DATA/AUG/EQDSK/EQDSK" ]]
# An existing DATA branch takes precedence over the GitHub branch.
git -C "$scratch/source" switch -qc data-feature
printf 'feature\n' > "$scratch/source/AUG/EQDSK/reference"
git -C "$scratch/source" commit -qam 'Feature data'
DATA_BRANCH=data-feature "$repo_root/scripts/fetch_data.sh" AUG/EQDSK > "$scratch/branch.log" 2>&1
cmp "$scratch/source/AUG/EQDSK/reference" "$DATA/AUG/EQDSK/reference"
# A broken checkout must fail instead of silently copying nothing.
git -C "$scratch/source" branch -m main other
if "$repo_root/scripts/fetch_data.sh" AUG/EQDSK > "$scratch/failure.log" 2>&1; then
    echo 'Expected missing fallback branch failure' >&2; exit 1
fi
# LFS transfer failures must be visible, never copied as pointer text.
git -C "$scratch/source" branch -m other main
printf '*.bin filter=lfs diff=lfs merge=lfs -text\n' > "$scratch/source/AUG/EQDSK/.gitattributes"
printf 'version https://git-lfs.github.com/spec/v1\noid sha256:%064d\nsize 123\n' 0 > "$scratch/source/AUG/EQDSK/missing.bin"
git -C "$scratch/source" -c filter.lfs.clean=cat -c filter.lfs.required=false add AUG/EQDSK/.gitattributes AUG/EQDSK/missing.bin
git -C "$scratch/source" commit -qm 'Unavailable LFS object'
if DATA_BRANCH=data-feature "$repo_root/scripts/fetch_data.sh" AUG/EQDSK > "$scratch/lfs.log" 2>&1; then
    echo 'Expected unavailable LFS object failure' >&2; exit 1
fi
[[ ! -e "$DATA/AUG/EQDSK/missing.bin" ]]
printf 'fetch_data regression checks passed\n'
