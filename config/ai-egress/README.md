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
