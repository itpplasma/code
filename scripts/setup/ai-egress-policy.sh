#!/usr/bin/env bash
# Render/check/apply the host-side forwarding policy for AI Incus containers.
# This is opt-in and only matches the configured Incus source subnet.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "${SCRIPT_DIR}/../.." && pwd)
CONFIG_DIR=${AI_EGRESS_CONFIG_DIR:-${ROOT_DIR}/config/ai-egress}
DOMAIN_FILE=${AI_EGRESS_DOMAIN_FILE:-${CONFIG_DIR}/deny-domains.txt}
NETWORK_FILE=${AI_EGRESS_NETWORK_FILE:-${CONFIG_DIR}/deny-networks.txt}
BRIDGE=${AI_EGRESS_BRIDGE:-incusbr0}
SUBNET=${AI_EGRESS_SUBNET:-10.234.0.0/24}
DNS_IP=${AI_EGRESS_DNS_IP:-10.234.0.1}
MODE=${AI_EGRESS_MODE:-baseline}
PROXY_IP=${AI_EGRESS_PROXY_IP:-10.234.0.1}
PROXY_PORT=${AI_EGRESS_PROXY_PORT:-3128}
TABLE=${AI_EGRESS_TABLE:-ai_egress}
ACTION=check

usage() {
    cat <<'EOF'
Usage: ai-egress-policy.sh [--check|--dry-run|--apply]

Environment overrides are documented in config/ai-egress/policy.env.example.
The default is --check. --apply requires root and replaces only this script's
own nftables table; it never flushes unrelated tables.
EOF
}

die() { echo "ai-egress-policy: $*" >&2; exit 1; }

while (($#)); do
    case "$1" in
        --check) ACTION=check ;;
        --dry-run) ACTION=dry-run ;;
        --apply) ACTION=apply; ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
    shift
done

[[ -r ${DOMAIN_FILE} ]] || die "domain list is not readable: ${DOMAIN_FILE}"
[[ -r ${NETWORK_FILE} ]] || die "network list is not readable: ${NETWORK_FILE}"
[[ ${MODE} == baseline || ${MODE} == proxy ]] || die "AI_EGRESS_MODE must be baseline or proxy"
[[ ${SUBNET} == */* && ${DNS_IP} != */* ]] || die "invalid subnet or DNS address"
if [[ ${MODE} == proxy ]]; then
    [[ ${PROXY_IP} != */* ]] || die "invalid proxy address"
    [[ ${PROXY_PORT} =~ ^[0-9]+$ && ${PROXY_PORT} -ge 1 && ${PROXY_PORT} -le 65535 ]] ||
        die "invalid proxy port"
fi

read_values() {
    local file=$1 line
    while IFS= read -r line || [[ -n ${line} ]]; do
        line=${line%%#*}
        line=${line//[[:space:]]/}
        [[ -n ${line} ]] && printf '%s\n' "${line}"
    done < "${file}"
}

mapfile -t NETWORKS < <(read_values "${NETWORK_FILE}")
mapfile -t DOMAINS < <(read_values "${DOMAIN_FILE}")
((${#NETWORKS[@]})) || die "network list is empty"
((${#DOMAINS[@]})) || die "domain list is empty"

# Resolve at render time only. Unresolved names are reported but do not abort a
# dry run; strict proxy mode does not depend on these addresses for safety.
mapfile -t DENIED_IPS < <(
    for domain in "${DOMAINS[@]}"; do
        getent ahostsv4 "${domain}" 2>/dev/null ||
            echo "unresolved:${domain}" >&2
    done | awk '$1 ~ /^[0-9]+(\.[0-9]+){3}$/ {print $1}' | sort -u
)

join_csv() {
    local first=1 value
    for value in "$@"; do
        if ((first)); then first=0; else printf ', '; fi
        printf '%s' "${value}"
    done
}

render() {
    cat <<EOF
table inet ${TABLE} {
    set denied_networks {
        type ipv4_addr
        flags interval
        elements = { $(join_csv "${NETWORKS[@]}") }
    }
    set denied_service_ips {
        type ipv4_addr
        flags interval
        elements = { $(join_csv "${DENIED_IPS[@]}") }
    }
    chain forward {
        type filter hook forward priority filter; policy accept;
        # DNS/DHCP to the Incus gateway is allowed before RFC1918 rejection.
        iifname "${BRIDGE}" ip saddr ${SUBNET} ip daddr ${DNS_IP} udp dport 53 accept
        iifname "${BRIDGE}" ip saddr ${SUBNET} ip daddr ${DNS_IP} tcp dport 53 accept
        iifname "${BRIDGE}" ip saddr ${SUBNET} ip daddr ${DNS_IP} udp dport 67 accept
        iifname "${BRIDGE}" ip saddr ${SUBNET} udp dport 53 drop
        iifname "${BRIDGE}" ip saddr ${SUBNET} tcp dport 53 drop
        iifname "${BRIDGE}" ip saddr ${SUBNET} tcp dport 853 drop
        iifname "${BRIDGE}" ip saddr ${SUBNET} ip daddr @denied_service_ips drop
EOF
    if [[ ${MODE} == proxy ]]; then
        cat <<EOF
        # High-assurance mode: only the configured policy proxy may carry
        # traffic. The final rule drops every other forwarded packet.
        iifname "${BRIDGE}" ip saddr ${SUBNET} ip daddr ${PROXY_IP} tcp dport ${PROXY_PORT} accept
        iifname "${BRIDGE}" ip saddr ${SUBNET} drop
EOF
    else
        cat <<EOF
        iifname "${BRIDGE}" ip saddr ${SUBNET} ip daddr @denied_networks drop
EOF
    fi
    cat <<EOF
        # Incus is provisioned with IPv6 disabled; fail closed if that changes.
        iifname "${BRIDGE}" meta nfproto ipv6 drop
EOF
    cat <<EOF
    }
}
EOF
}

case ${ACTION} in
    check)
        command -v nft >/dev/null || die "nft is required"
        command -v getent >/dev/null || die "getent is required"
        echo "configuration OK (${MODE} mode; ${#DOMAINS[@]} denied hostnames; ${#NETWORKS[@]} denied networks)"
        if [[ ${MODE} == baseline ]]; then
            echo "warning: baseline hostname/IP set is not a complete direct-IP or CDN boundary"
            echo "use proxy mode with a policy-aware gateway for that guarantee"
        fi
        ;;
    dry-run)
        render
        ;;
    apply)
        [[ ${EUID} -eq 0 ]] || die "--apply must run as root"
        tmp=$(mktemp)
        trap 'rm -f "${tmp}"' EXIT
        render > "${tmp}"
        # A table declaration creates the table, so remove only our own old
        # table before installing the generated replacement. No other nft
        # table, chain, or host traffic is touched.
        if nft list table inet "${TABLE}" >/dev/null 2>&1; then
            nft delete table inet "${TABLE}"
        fi
        nft -c -f "${tmp}"
        nft -f "${tmp}"
        echo "applied table inet ${TABLE} for ${BRIDGE}/${SUBNET} (${MODE} mode)"
        ;;
esac
