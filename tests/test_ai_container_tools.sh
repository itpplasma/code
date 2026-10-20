#!/usr/bin/env bash
# Behavioral test for the container tool installer. The fake Incus command
# models the host/guest boundary and proves dry-run never executes guest work.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
cat >"$tmp/bin/incus" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${AI_TEST_LOG:?}"
case "${1:-}" in
  info) exit 0 ;;
  exec) exit 0 ;;
  *) echo "unexpected guest mutation in dry-run" >&2; exit 99 ;;
esac
SH
chmod +x "$tmp/bin/incus"

run_dry() {
    : >"$tmp/calls"
    PATH="$tmp/bin:$PATH" AI_TEST_LOG="$tmp/calls" USER=ert \
        "$repo_root/scripts/setup/ai-container-tools.sh" "$@"
}

local_output="$(run_dry ai-local --dry-run)"
grep -Fq 'opencode2, pi, dsh, slopshell' <<<"$local_output"
grep -Fq 'would not install cloud CLIs' <<<"$local_output"
if grep -Eq 'codex|claude' <<<"$local_output"; then
    echo 'local dry-run unexpectedly selected cloud tools' >&2
    exit 1
fi
grep -Fxq 'info ai-local' "$tmp/calls"
[[ "$(wc -l <"$tmp/calls")" -eq 1 ]]

: >"$tmp/calls"
PATH="$tmp/bin:$PATH" AI_TEST_LOG="$tmp/calls" USER=ert \
    "$repo_root/scripts/setup/ai-container-tools.sh" --container ai-local --check
grep -Fq 'exec ai-local' "$tmp/calls"

cloud_output="$(run_dry --container ai-cloud --cloud --dry-run)"
grep -Fq 'Codex CLI, Claude Code' <<<"$cloud_output"
grep -Fxq 'info ai-cloud' "$tmp/calls"
[[ "$(wc -l <"$tmp/calls")" -eq 1 ]]

if grep -Eiq 'api\.key|\.ssh|/home/ert/\.config' <<<"$local_output"; then
    echo 'dry-run advertised host credential transfer' >&2
    exit 1
fi
echo 'PASS: container tool installer selects local/cloud tools without host mutation'
