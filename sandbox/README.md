# Agent-zone foundation

This library in [the infrastructure repository](https://github.com/itpplasma/code)
provides offline building blocks for the fixed `internal` and `confidential`
Debian virtual machines (VMs). It does not deploy host services or provide a
complete `ai` command. Production use requires the acceptance gates below.

## Available components

| Module | Implemented scope |
|---|---|
| [`policy.py`](ai_zones/policy.py) | Strict, immutable policy schema; explicit protected-path loading; root ownership, modes, symlink and native macOS access-control checks. |
| [`release.py`](ai_zones/release.py) | Offline public-key signature and payload-hash verification; root-only tool installation from retained verified bytes with atomic `current` selection. |
| [`vm.py`](ai_zones/vm.py) | Fixed vfkit and QEMU command builders, resource validation and distinct Secure Shell (SSH) transport helpers; builders never launch a VM. |
| [`guest.py`](ai_zones/guest.py) | Debian 13 raw-image checksum verification, NoCloud seed generation and fixed guest relay source and units. |
| [`confinement.py`](ai_zones/confinement.py) | macOS Seatbelt profile and launch-argument generation with a tested-build gate; an explicitly untested Linux systemd namespace fragment. |
| [`runtime.py`](ai_zones/runtime.py) | Bounded signed Python runtime manifests and verification, plus a root-only installation interface separate from the tool updater. |

[`policy.example.json`](policy.example.json) contains synthetic paths and model
settings. Schema validation alone grants no authority. Protected loading requires
an explicit normalized absolute path whose complete ancestry passes protection
checks; environment variables and the current directory do not select policy.
Confidential model origins must use literal loopback addresses, without name
resolution.

## Release and runtime trust

Tool bundles contain `manifest.json`, `manifest.sig` and `payload/`. Verification
uses the supplied RSA public key, SHA-256 hashes and local `/usr/bin/openssl`;
it downloads nothing and runs no payload code. Manifests allow bounded Python files and named entry
points, with hashes for every payload file. Verification with a caller-selected
key is a consistency check, not deployment approval.

Production installation requires an independently approved, frozen privileged
core, its Python dependencies, protected public keys and a root-controlled
destination. Ordinary signed tool releases must neither replace that core nor
be imported by its privileged installer. Keep signing keys outside agent and
service-account access. Policy, credentials and revocations remain outside tool
releases; selecting older code must not restore older authority.

The Python runtime has its own signed complete-tree manifest and trust key.
Its current schema supports pinned macOS 64-bit Arm Python bundles; a Linux prefix
in the installer does not establish Linux runtime support. Bundle verification
does not turn mutable source files into a trusted installed interpreter. Runtime
installation and launch-time integrity checks need their own approval and tests;
the runtime command line exposes build and verify operations, not installation.

## Fixed guest channels

macOS uses vfkit with raw SSH over its fixed Unix socket. Linux uses QEMU with
one `vhost-device-vsock` backend per VM and zone-specific identities and groups;
its SSH helper exchanges `CONNECT 2222` / `OK 2222` before SSH bytes. These
transports are distinct and must not share the same connection helper.

Guest relays expose only virtual-socket port 2222 to guest-local SSH port 22,
and guest-local Transmission Control Protocol (TCP) port 5001 to host
virtual-socket port 5001. Command builders
emit no network interface, shared host filesystem or management interface.
Arguments alone do not prove isolation at runtime.

Seed generation writes staging files. Applying that seed later inside a guest
installs and starts its SSH and broker relays. It does not prepare missing
packages or fetch dependencies. No host services are installed or activated by
generating seeds, profiles, unit fragments or VM arguments.

## Verification and deployment gates

From the repository root, run the affected standard-library unittest suite:

```sh
python3 -m unittest discover -s tests -p 'test_agent_zone_*.py' -q
```

Tests use synthetic data, temporary signing/SSH keys and local sockets. They
require `/usr/bin/openssl` and `/usr/bin/ssh-keygen`; the SSH configuration probe
also uses an available SSH server. Live Seatbelt tests require macOS, their
canonical test interpreter and the exact build named in `confinement.py`.
Optional staged-runtime probes use `AI_ZONES_TEST_RUNTIME_ROOT`; shared-policy
integration probes use `AI_ZONES_POLICY_SOURCE`. Inspect skips before claiming
that a platform or interpreter passed.

Before admitting real data, verify the entire protected execution chain and
signed installed runtime, reserve distinct ports for different service roles,
close inherited descriptors, clear the environment and set a fixed working
directory. Retest Seatbelt after a system or runtime change. Its measured
exceptions expose top-level root names and, when enabled, public system and
hostname information.
Linux needs real namespace, isolated-root and Unix model-adapter probes.

Boot both guests and test pinned SSH identities, persistence, resource and log
limits, absent network/shares, arbitrary-port denial and sibling-zone denial.
Approve image provenance separately from checksum matching. Validate scoped
service authorization, updates, rollback and encrypted recovery before cutover.
The full deployment acceptance plan is in [agent-zones.md](../docs/agent-zones.md).
