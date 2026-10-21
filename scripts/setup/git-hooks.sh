#!/usr/bin/env bash
# Optional global hygiene hook. Missing tools leave the current configuration alone.
set -euo pipefail
warn() { printf 'Warning: %s; repository hygiene hook installation skipped.\n' "$*" >&2; }
for tool in git python3 curl install mktemp; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    warn "missing $tool (install it and rerun scripts/setup/git-hooks.sh)"
    exit 0
  fi
done
revision=04da3fc73d9ead713b93700de7bbf54049b5ac3e
checker_sha256=849afd7097350fde0af1863c258bad09a6b75d9a378266001798335232046e59
hook_sha256=7d5cd9daa40ffca2b7526657f0f4aa169c125d65e448007a8dfa840894ba8eee
dest="$HOME/.local/share/git-hooks"
if [[ "${1:-}" == --dry-run ]]; then
  printf 'Would install global repository hygiene %s at %s\n' "$revision" "$dest"
  exit 0
fi
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
if ! curl -fsSL --connect-timeout 5 --max-time 20 \
  "https://raw.githubusercontent.com/itpplasma/.github/$revision/scripts/repo_hygiene.py" -o "$tmp/repo_hygiene.py"; then
  warn 'cannot download the pinned checker'
  exit 0
fi
# The public canonical hook is shared with the private prompts installer.
install -m 755 "$(dirname "${BASH_SOURCE[0]}")/pre-commit" "$tmp/pre-commit"
python3 - "$tmp" "$checker_sha256" "$hook_sha256" <<'VERIFY'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
for name, digest in zip(('repo_hygiene.py', 'pre-commit'), sys.argv[2:]):
    if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
        raise SystemExit('SHA256 mismatch: ' + name)
VERIFY
mkdir -p "$dest/bin"
if command -v gitleaks >/dev/null 2>&1; then
  if [[ ! "$(command -v gitleaks)" -ef "$dest/bin/gitleaks" ]]; then
    install -m 755 "$(command -v gitleaks)" "$dest/bin/gitleaks"
  fi
else
  echo 'gitleaks not found: secret checks will warn and skip until it is installed.' >&2
fi
if curl -fsSL --connect-timeout 1 --max-time 2 \
  https://api.github.com/repos/lazy-fortran/fo/releases/latest -o "$tmp/fo-latest.json"; then
  install -m 644 "$tmp/fo-latest.json" "$dest/fo-latest.json"
fi
install -m 644 "$tmp/repo_hygiene.py" "$dest/repo_hygiene.py"
install -m 755 "$tmp/pre-commit" "$dest/pre-commit"
printf '%s\n' "$revision" > "$dest/revision"
git config --global core.hooksPath "$dest"
printf 'Installed global repository hygiene %s at %s\n' "$revision" "$dest"
