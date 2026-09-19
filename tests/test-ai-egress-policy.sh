#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT=${ROOT}/scripts/setup/ai-egress-policy.sh

output=$(${SCRIPT} --dry-run 2>/dev/null)
grep -Fq 'tcp dport 53 drop' <<<"${output}"
grep -Fq 'ip daddr @denied_networks drop' <<<"${output}"
grep -Fq 'ip daddr @denied_service_ips drop' <<<"${output}"
grep -Fq 'ip daddr 10.77.0.1 udp dport 53 accept' <<<"${output}"
grep -Fq 'ip daddr 10.77.0.1 tcp dport 3128 accept' <<<"${output}"

dns_allow=$(grep -n 'ip daddr 10.77.0.1 udp dport 53 accept' <<<"${output}" | cut -d: -f1)
private_drop=$(grep -n 'ip daddr @denied_networks drop' <<<"${output}" | cut -d: -f1)
((dns_allow < private_drop))

proxy_output=$(AI_EGRESS_MODE=proxy ${SCRIPT} --dry-run 2>/dev/null)
grep -Fq 'ip daddr 10.77.0.1 tcp dport 3128 accept' <<<"${proxy_output}"
grep -Fq 'ip saddr 10.77.0.0/24 drop' <<<"${proxy_output}"
if grep -Fq 'ip daddr @denied_networks drop' <<<"${proxy_output}"; then
    exit 1
fi

echo 'ai-egress-policy: render tests passed'
