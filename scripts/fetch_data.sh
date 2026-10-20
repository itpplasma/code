#!/usr/bin/env bash
set -euo pipefail

# Fetch selected DATA subtrees with a read-only token. No token means no fetch.
# Usage: scripts/fetch_data.sh <subdir> [<subdir> ...]
if [[ -z "${GITLAB_ACCESS_TOKEN:-}" ]]; then
    echo "GITLAB_ACCESS_TOKEN is not set; skipping DATA fetch"
    exit 0
fi
if [[ $# -eq 0 ]]; then
    echo "usage: $0 <subdir> [<subdir> ...]" >&2
    exit 2
fi
for subdir in "$@"; do
    case "/$subdir/" in
        //*|*/../*|*/./*) echo "Expected a relative DATA subtree: $subdir" >&2; exit 2 ;;
    esac
done

repo_url="${GITLAB_DATA_REPO_URL:-https://gitlab.tugraz.at/plasma/data.git}"
data_root="${DATA:-$PWD/.testdata}"
[[ "$data_root" = /* ]] || data_root="$PWD/$data_root"
branch="${DATA_BRANCH:-${GITHUB_HEAD_REF:-${GITHUB_REF_NAME:-main}}}"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
# Keep the token out of process arguments, logs and the clone's remote URL.
cat > "$tmpdir/askpass" <<'ASKPASS'
#!/usr/bin/env bash
case "$1" in
    *Username*) echo oauth2 ;;
    *Password*) printf '%s\n' "$GITLAB_ACCESS_TOKEN" ;;
esac
ASKPASS
chmod 700 "$tmpdir/askpass"
export GIT_ASKPASS="$tmpdir/askpass" GIT_TERMINAL_PROMPT=0 GIT_LFS_SKIP_SMUDGE=1

echo "Fetching DATA subtrees ($*) on branch $branch"
git -c credential.helper= clone --filter=blob:none --sparse --no-checkout "$repo_url" "$tmpdir/data"
cd "$tmpdir/data"
if ! git show-ref --verify --quiet "refs/remotes/origin/$branch"; then
    echo "Branch $branch not found in data repo; falling back to main"
    branch=main
fi
git sparse-checkout set -- "$@"
git checkout --detach "refs/remotes/origin/$branch"

# Detect selected pointer files, including repositories with nested attributes.
if git grep -l '^version https://git-lfs.github.com/spec/v1$' -- "$@" >/dev/null; then
    git lfs version >/dev/null
    includes=()
    for subdir in "$@"; do includes+=("$subdir/**"); done
    include_list="$(IFS=,; echo "${includes[*]}")"
    git -c credential.helper= lfs pull --include="$include_list" --exclude=""
fi

mkdir -p "$data_root"
for subdir in "$@"; do
    if [[ ! -d "$subdir" ]]; then
        echo "Subtree $subdir not found in data repo; skipping"
        continue
    fi
    mkdir -p "$data_root/$subdir"
    cp -a "$subdir/." "$data_root/$subdir/"
done
echo "DATA subtrees synchronized to $data_root"
