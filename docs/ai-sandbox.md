# Incus AI sandbox

A single long-lived, unprivileged Debian trixie container for running coding
agents and shells against your real working directories, with internet access
but no reach into the LAN, the host, or any host path you have not explicitly
attached.

It is the local, lightweight sibling of the Multipass AI VM
(`scripts/multipass-ai.sh`). Multipass gives a full VM with its own kernel and
mounts nothing; this gives a container that starts in under a second and keeps
the whole `~/code` and `~/proj` workspace visible in every session.

## Model

One container named `ai`, never rebuilt per run. Sessions are just
`incus exec` into it, so installed packages, caches, and agent logins are simply
there. Concurrency is handled by attaching each working directory as its own
hotplugged disk device:

```
host  ~/code      ──┐
host  ~/proj      ──┼──>  container ai  ──>  internet (NAT)
host  ~/Downloads ──┘                    ✗   LAN / host / WireGuard
```

The optional trust-domain setup adds `ai-local` and `ai-cloud`, each with a
separate persistent home. The existing `ai` instance remains the fallback and
is not modified by that setup. Both profile instances receive the read-only
prompts checkout and use the same session mount rules:

```bash
scripts/setup/incus-ai-profiles.sh
ai                  # ai-local when it exists, otherwise legacy ai
ai local pi         # local profile explicitly
ai cloud claude     # cloud profile explicitly
```

Three properties make this usable:

- **Directories appear at their real absolute path.** `/home/ert/code/foo` on
  the host is `/home/ert/code/foo` inside. Paths in compiler output, agent
  messages, stack traces, and `compile_commands.json` line up on both sides, and
  two projects can never collide on one mount point.
- **The guest user is you.** Same name, same uid. Combined with idmapped mounts
  the container writes files that are already owned by your host user; no
  `chown` dance, no root-owned build artifacts.
- **The workspace roots are always mounted.** Every session sees all checkouts
  below `~/code` and `~/proj`, including when it starts in an individual code
  directory. An additional directory outside those roots is attached only for
  that session and detached when the last session in it exits.

The cloud selector refuses current directories under `~/Nextcloud`,
`~/Dropbox`, and the local brain trees; use `ai local` for those paths.

## Use

Put the launcher on your PATH as `ai`, which is also the container's name:

```bash
ln -s ~/code/infra/scripts/ai-sandbox.sh ~/bin/ai
```

```bash
cd ~/code/some-project
ai                        # interactive shell, in this directory
ai claude                 # run a command here (once installed)
cd ~/proj/another-project && ai  # same sandbox, all code and projects visible
ai --root apt-get install -y ripgrep
ai status                 # attached directories and session counts
ai detach [DIR]           # force-detach
ai prune                  # drop mounts whose sessions are gone
ai stop
```

The launcher prepends `--dangerously-skip-permissions` to `claude` and
`--yolo --search` to `codex`; `dsh`, `opencode`, and `pi` receive supplied
arguments unchanged. The profile setup may add an Incus proxy device for the
host llama.cpp endpoint (`AI_LLAMA_PORT`, default `8080`). The proxy listens
only on the container loopback (`bind=container`); it is not a host/LAN port.
Unsupported proxy devices are reported without changing host services or MCP
registrations.

The container ships with **no software installed** beyond the stock Debian
trixie image — deliberately. Install what a task needs with
`--root apt-get install`, and when the set stabilises move it into a script here
so the sandbox stays reproducible.

## Provisioning

```bash
scripts/setup/incus-ai-sandbox.sh
```

Idempotent; it creates only what is missing. It builds:

| Object | Name | Notes |
|---|---|---|
| Storage pool | `storage` | btrfs, on the `/mnt/storage/incus` subvolume |
| Bridge | `incusbr0` | `10.234.0.1/24`, IPv4 NAT, IPv6 off |
| Profile bridge | `ai0` | `10.77.0.1/24`, IPv4 NAT, IPv6 off; local/cloud only |
| Network ACL | `ai-sandbox-egress` | drops RFC1918, allows the rest |
| Profile | `ai-sandbox` | bridge NIC with the ACL, root disk on the pool |
| Volume | `ai-home` | persistent `/home/<user>` |
| Instance | `ai` | `images:debian/13` |

Prerequisites on the host: `incus`, your user in `incus-admin`, and a `root`
range in `/etc/subuid` and `/etc/subgid`. The script checks all three and tells
you what to run.

### Install tools inside a profile

After creating the two trust-domain instances, install or update their tools
from the host with the container installer. It performs all downloads and
writes inside Incus; it never copies host credentials or changes host MCP
registrations.

```bash
scripts/setup/incus-ai-profiles.sh
scripts/setup/ai-container-tools.sh --container ai-local
scripts/setup/ai-container-tools.sh --container ai-cloud --cloud
scripts/setup/ai-container-tools.sh --container ai-local --check
```

Both profiles receive local `opencode2`, `pi`, `dsh`, and `slopshell`. The
cloud profile additionally receives the official Codex and Claude Code
installers. The prompts checkout is mounted read-only at
`/home/<user>/prompts`; its installer wires skills and provider templates into
the private guest home. Authentication must be performed separately in the
selected container: this installer deliberately does not import host
`~/.codex`, `~/.claude`, browser state, SSH keys, or API-key files.

Use `--dry-run` first to inspect the trust-domain-specific operation:

```bash
scripts/setup/ai-container-tools.sh --container ai-cloud --cloud --dry-run
```

## Network policy

The local/cloud profiles use the dedicated `ai0` bridge; the legacy `ai`
instance remains on `incusbr0` while this migration is tested. Egress default
is allow, with explicit drops for `10.0.0.0/8`,
`172.16.0.0/12`, `192.168.0.0/16`, and `169.254.0.0/16`. Ingress default is
drop. So the sandbox can reach the public internet and nothing else: not the
LAN, not Nextcloud, not the Macs over WireGuard at `10.77.0.0/24`, not a service
listening on the host.

**Rule order matters.** The profile bridge gateway that serves DNS and DHCP is
`10.77.0.1`, which sits inside the `10.0.0.0/8` drop. The allow rules for port
53 and 67 to `10.77.0.1/32` must come first, or the container has no name
resolution. `incus network acl show ai-sandbox-egress` prints them in
evaluation order.

Verify from inside:

```bash
scripts/ai-sandbox.sh bash -c '
  timeout 5 bash -c "exec 3<>/dev/tcp/1.1.1.1/443"    && echo "internet OK"
  timeout 5 bash -c "exec 3<>/dev/tcp/192.168.1.1/80" || echo "LAN blocked"
  timeout 5 bash -c "exec 3<>/dev/tcp/10.77.0.1/22"  || echo "host blocked"'
```

A blocked destination hangs until the timeout rather than refusing, because the
rule action is `drop`, not `reject`.

## What this does and does not protect against

It is a container, not a VM: the kernel is shared with the host. It is
unprivileged, so container root maps to an unused high host uid, and it sees
only the host paths you attach. That is a solid boundary against a confused or
destructive agent — the case this is built for.

It is a weaker boundary than a VM against a kernel exploit, and **AppArmor is
disabled on this kernel**, so that layer is absent. If you ever need to run
genuinely untrusted code, use an Incus VM or the Multipass profile instead.

Note also that `incus-admin` membership is equivalent to host root for your own
account, since it can bind-mount any path into a container. It does not weaken
the container's isolation, but it is not a boundary around your own account.

Finally, `/mnt/storage` is a RAID0 with no redundancy. The sandbox home volume
lives there. Either SSD failing loses it.

## Implementation notes

Things that were not obvious while building this, kept so the next change does
not rediscover them.

**The daemon needs its own subuid range.** With no `root:` line in
`/etc/subuid` and `/etc/subgid`, every container fails at creation with
`System doesn't have a functional idmap setup`. `root:1000000:1000000000` is
the conventional range.

**Use idmapped mounts, not `raw.idmap`.** The obvious way to make host files
appear owned by the guest user is `raw.idmap: both 1000 1000` on the profile.
It fails at start with
`newuidmap: uid range [1000-1001) -> [1000-1001) not allowed`, because mapping
host uid 1000 requires a matching `root:1000:1` entry in `/etc/subuid`. Setting
`shift=true` on each disk device is better: Incus idmaps that mount alone, so
ownership is right on both sides with no host-wide uid mapping and no extra
`/etc/subuid` entry. Verified in both directions — a file written in the
container lands owned by your host user, and vice versa.

**Concurrent device adds race on the API ETag.** Adding a device rewrites the
whole instance config, so two sessions attaching different directories at the
same moment produce
`Error: ETag doesn't match: <hash> vs <hash>` and one session dies. The
launcher therefore holds a single instance-wide `flock` around the
`incus config device add`/`remove` call. Per-directory locks are not enough.
The lock covers only the config call, never the session, so sessions still run
fully in parallel. This was caught by launching four concurrent sessions across
three directories; that is the regression test worth repeating after changes.

**Detaching leaves an empty directory skeleton.** The mount point itself
survives in the home volume, so without cleanup the sandbox accumulates an
empty mirror of every path ever attached. The launcher walks up from the mount
point after removing the device, `rmdir`-ing until a directory is non-empty or
it reaches the home. `rmdir` never touches a non-empty directory, so real
container content stops the walk.

**Sessions killed with SIGKILL leave a stale reference.** The `EXIT` trap does
not run, so the refcount never reaches zero and the mount stays. Attach records
are named by pid, and both `detach_if_idle` and `prune` drop records whose pid
is gone.

**`/home/<user>` is both the persistent volume and a mount parent.** Because
directories are attached at their real paths, a project under your home becomes
a mount point inside the home volume. That works, but attaching `/home/<user>`
itself would shadow the volume, so the launcher refuses that path along with
`/`, `/home`, `/mnt`, and `/mnt/storage`.

**Image choice.** `images:debian/13` is the minimal trixie container rootfs
(~100 MiB). `images:debian/13/cloud` adds cloud-init and is the right base only
if provisioning through cloud-init, as `cloud-init/multipass-ai.yaml` does.
