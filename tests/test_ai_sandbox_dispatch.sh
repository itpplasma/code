#!/usr/bin/env bash
# Behavioral test for the launcher dispatch policy.  The fake Incus command
# models the observable exec boundary, rather than asserting source text.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
mkdir -p "${tmp}/home/code/project" "${tmp}/home/proj/project" "${tmp}/bin" "${tmp}/runtime"

cat >"${tmp}/bin/incus" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
log="${AI_TEST_LOG:?}"
printf '%s\n' "$*" >>"${log}"
case "${1:-}" in
  info)
    if [[ "${2:-}" == ai || "${2:-}" == ai-local || "${2:-}" == ai-cloud || -z "${2:-}" ]]; then
      [[ "${2:-}" != ai-local || "${AI_TEST_LOCAL_MISSING:-0}" != 1 ]] || exit 1
      [[ -z "${2:-}" ]] || printf 'Status: Running\n'
      exit 0
    fi
    exit 1
    ;;
  exec)
    if [[ "$*" == *' id -u '* ]]; then printf '1000\n'; exit 0; fi
    if [[ "$*" == *' id -g '* ]]; then printf '1000\n'; exit 0; fi
    exit 0
    ;;
  config|start) exit 0 ;;
  *) exit 0 ;;
esac
SH
chmod +x "${tmp}/bin/incus"

run() {
  : >"${tmp}/calls"
  (cd "${tmp}/home/code/project" && \
    HOME="${tmp}/home" USER=ert XDG_RUNTIME_DIR="${tmp}/runtime" \
    PATH="${tmp}/bin:${PATH}" AI_TEST_LOG="${tmp}/calls" \
    "${repo_root}/scripts/ai-sandbox.sh" "$@") >/dev/null
  cat "${tmp}/calls"
}

default_call="$(run claude --help)"
grep -Fq 'exec ai-cloud' <<<"${default_call}"
grep -Fq -- 'claude --dangerously-skip-permissions --help' <<<"${default_call}"

cloud_call="$(run cloud codex inspect)"
grep -Fq 'exec ai-cloud' <<<"${cloud_call}"
grep -Fq -- 'codex --yolo --search inspect' <<<"${cloud_call}"

root_cloud_call="$(run --root cloud true)"
grep -Fq 'exec ai-cloud' <<<"${root_cloud_call}"
grep -Fq -- '-- true' <<<"${root_cloud_call}"

for selector in --cloud --local; do
  call="$(run "${selector}" true)"
  expected=ai-cloud
  [[ "${selector}" == --local ]] && expected=ai-local
  grep -Fq "exec ${expected}" <<<"${call}"
done

for args in "--root --cloud" "--cloud --root"; do
  # shellcheck disable=SC2086
  call="$(run ${args} true)"
  grep -Fq 'exec ai-cloud' <<<"${call}"
  grep -Fq -- '-- true' <<<"${call}"
done

for tool in dsh opencode pi; do
  call="$(run cloud "${tool}" --version)"
  grep -Fq "exec ai-cloud" <<<"${call}"
  grep -Fq -- "${tool} --version" <<<"${call}"
done

echo "PASS: ai dispatches trust domain and tool aliases at the exec boundary"

: >"${tmp}/calls"
(cd "${tmp}/home/code/project" && \
  HOME="${tmp}/home" USER=ert XDG_RUNTIME_DIR="${tmp}/runtime" \
  PATH="${tmp}/bin:${PATH}" AI_TEST_LOG="${tmp}/calls" AI_TEST_LOCAL_MISSING=1 \
  "${repo_root}/scripts/ai-sandbox.sh" true) >/dev/null
grep -Fq 'exec ai --cwd' "${tmp}/calls"
echo "PASS: ai falls back to the legacy instance"
