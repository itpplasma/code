#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT=${ROOT}/scripts/setup/ai-egress-proxy.sh

output=$(AI_EGRESS_RESOLVE=0 "${SCRIPT}" --dry-run)
grep -Fq 'http_port 10.254.77.1:3128' <<<"${output}"
grep -Fq 'http_port 10.254.77.1:3129' <<<"${output}"
grep -Fq 'local-deny-domains.txt' <<<"${output}"
grep -Fq 'acl ai_clients src 10.254.77.0/24' <<<"${output}"
grep -Fq 'http_access deny !ai_clients' <<<"${output}"
grep -Fq 'http_access deny !Safe_ports' <<<"${output}"
grep -Fq 'acl ai_numeric_ip dstdom_regex -i' <<<"${output}"
grep -Fq 'http_access deny ai_numeric_ip' <<<"${output}"
grep -Fq 'http_access deny all' <<<"${output}"
if grep -Fq 'http_port 0.0.0.0' <<<"${output}"; then
    exit 1
fi

client_deny=$(grep -n 'http_access deny !ai_clients' <<<"${output}" | head -n1 | cut -d: -f1)
client_allow=$(grep -n 'http_access allow ai_clients' <<<"${output}" | head -n1 | cut -d: -f1)
((client_deny < client_allow))

if AI_EGRESS_SQUID_BIN=/does/not/exist "${SCRIPT}" --check >/dev/null 2>&1; then
    exit 1
fi

echo 'ai-egress-proxy: render tests passed'
