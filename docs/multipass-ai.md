# Multipass AI coding VM

The accepted plan for new agent deployments is
[Debian agent zones](agent-zones.md). The recipe below documents the existing
Ubuntu Multipass launcher.

`scripts/multipass-ai.sh` creates a headless Ubuntu LTS virtual machine for
coding agents on ARM64 and x86-64 hosts. The default size is 8 CPUs, 16 GB RAM
and a 128 GB disk:

    scripts/multipass-ai.sh
    scripts/multipass-ai.sh --name ai-work --cpus 12 --memory 24G

Resources can also be set with `MULTIPASS_AI_*` environment variables. The
launcher refuses the instance name `primary`, because Multipass may mount the
host home directory there, and it mounts no host directory itself. Work starts
in `~/workspace`; mount or copy other paths explicitly when needed.

[`cloud-init/multipass-ai.yaml`](../cloud-init/multipass-ai.yaml) calls the same
`scripts/setup/apt.sh ai` installer that can be run directly on Debian or
Ubuntu, so the package list is defined once.

## Installed tools

The VM provides Codex, Claude Code, OpenCode, Pi, uv, chezmoi, the GitHub and
GitLab CLIs, compilers, scientific libraries and common command-line tools.
CLI logins and provider tokens persist inside the VM; no credential is stored in
cloud-init or in Git.

Commands available inside the VM:

- `ai-update` updates the public AI command-line tools.
- `ai-private-tools` installs or updates the private Helpy and Sloptools MCP
  servers after `gh auth login`, with the Sloptools project root restricted to
  `~/workspace`.
- `ai-user-config <git-repository>` applies a personal chezmoi repository
  (age-encrypted files are supported); run it without arguments to update.

API keys that cannot be provided through a CLI login can be stored in
`~/.config/ai-infra/env` with mode `0600`; an example file is created next to
it. The file stays inside the VM unless you manage an encrypted copy with
chezmoi.

For a lighter container-based alternative that sees the host workspace, see
[ai-sandbox.md](ai-sandbox.md).
