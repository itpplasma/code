#!/usr/bin/env bash
# Optional global hygiene hook. Missing tools leave the current configuration alone.
set -euo pipefail
warn() { printf 'Warning: %s; repository hygiene hook installation skipped.\n' "$*" >&2; }
for tool in git python3 curl gitleaks install mktemp; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    warn "missing $tool (install it and rerun scripts/setup/git-hooks.sh)"
    exit 0
  fi
done
revision=0663fcdd3b414a741bc3df08959a6f83aedf2d40
checker_sha256=816a3f1f201003022bf2b9e10d98f7acdd4f4d5067d0b19585768ae1c50deb9b
hook_sha256=76f3740318ac4bc4769bbaa9ebcb3b8197ae2701980e309009967d241dde669e
dest="$HOME/.local/share/git-hooks"
if [[ "${1:-}" == --dry-run ]]; then
  printf 'Would install global repository hygiene %s at %s\n' "$revision" "$dest"
  exit 0
fi
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
for file in repo_hygiene.py pre-commit; do
  if ! curl -fsSL "https://raw.githubusercontent.com/itpplasma/.github/$revision/scripts/$file" -o "$tmp/$file"; then
    warn "cannot download $file"
    exit 0
  fi
done
python3 - "$tmp" "$checker_sha256" "$hook_sha256" <<'VERIFY'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
for name, digest in zip(('repo_hygiene.py', 'pre-commit'), sys.argv[2:]):
    if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
        raise SystemExit('SHA256 mismatch: ' + name)
VERIFY
mkdir -p "$dest/bin"
if [[ ! "$(command -v gitleaks)" -ef "$dest/bin/gitleaks" ]]; then
  install -m 755 "$(command -v gitleaks)" "$dest/bin/gitleaks"
fi
install -m 644 "$tmp/repo_hygiene.py" "$dest/repo_hygiene.py"
install -m 755 "$tmp/pre-commit" "$dest/pre-commit"
printf '%s\n' "$revision" > "$dest/revision"
git config --global core.hooksPath "$dest"
printf 'Installed global repository hygiene %s at %s\n' "$revision" "$dest"
