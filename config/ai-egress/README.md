# AI container egress policy

This policy is deliberately split into two layers:

* `baseline` keeps ordinary public Internet access, blocks private/local
  destinations, denies known authenticated service addresses, and permits DNS
  only to the Incus bridge resolver.
* `proxy` is the enforceable high-assurance mode. It permits only the bridge
  DNS endpoint and one explicitly configured HTTP CONNECT/SOCKS gateway; all
  other forwarding from the AI subnet is dropped. The gateway must perform
  hostname/SNI policy and must not be reachable from the legacy host path.

DNS filtering alone is not sufficient. A model can use a literal IP, another
resolver, DoH/DoT, or a shared CDN address. The baseline set generated from
`deny-domains.txt` is therefore only defence in depth. Use proxy mode once a
policy-aware gateway is deployed, and keep the cloud container free of
credentials and sensitive mounts regardless of network mode.

The setup script is intentionally not enabled by installation. It supports
`--dry-run`, `--check`, and explicit `--apply`; applying it changes only the
forwarding rules whose source is the configured Incus subnet. It does not
flush existing tables or alter legacy host traffic.

```bash
scripts/setup/ai-egress-policy.sh --check
scripts/setup/ai-egress-policy.sh --dry-run
sudo scripts/setup/ai-egress-policy.sh --apply
```

The systemd unit/timer are templates for refreshing resolved deny addresses;
they are not enabled by this repository. A future deployment should run the
refresh in the same network namespace as the policy gateway and audit the
resulting nftables set.

## Strict proxy deployment

For a real direct-IP/alternate-DNS boundary, install the distribution's
official `squid` package and use the dedicated gateway renderer:

```bash
sudo apt install squid                 # explicit prerequisite, not automated
scripts/setup/ai-egress-proxy.sh --check
scripts/setup/ai-egress-proxy.sh --dry-run
sudo scripts/setup/ai-egress-proxy.sh --apply
sudo systemctl enable --now ai-egress-proxy.service
```

The generated Squid listener binds only to `10.234.0.1:3128`, allows only the
AI subnet, denies the domain/network/IP lists before the client allow, and
rejects numeric IPv4 destinations so a `CONNECT` request cannot bypass domain
policy by using a literal address. Public hostname-based HTTPS remains allowed.
It does not touch the legacy host service. Pair it with the nftables policy in
`AI_EGRESS_MODE=proxy`; that policy drops every AI-subnet packet except DNS and
the configured proxy endpoint. `--apply` installs and validates configuration
but deliberately does not start or enable the service.

This is an explicit CONNECT proxy: its hostname ACL applies to the CONNECT
destination. It does not inspect an arbitrary inner TLS SNI while tunnelling.
If shared-CDN/SNI mismatch is part of the threat model, use a TLS-aware
policy gateway (or a dedicated public fetcher) instead of treating Squid alone
as a complete hostname boundary.
