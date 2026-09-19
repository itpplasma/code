#!/usr/bin/env bash
# Install/update the agent tools inside one Incus AI trust-domain container.
# This script runs installers inside Incus only and never copies host
# credentials or registers MCP servers on the host.
set -euo pipefail

usage() {
    cat >&2 <<'EOF'
Usage: ai-container-tools.sh --container INSTANCE [options]
       ai-container-tools.sh INSTANCE [--cloud] [--dry-run|--check]

Install/update local tools (opencode2, pi, dsh, and the Slopshell gateway) in
the selected container. Add --cloud to install Codex and Claude Code too.

Options:
  --container NAME  Incus instance to modify (required)
  --cloud           install the cloud-only Codex and Claude Code CLIs
  --prompts PATH    prompts checkout path inside the container
  --dry-run         print guest operations without executing them
  --check           verify installed tools and prompt wiring without changes
EOF
}

instance=
cloud=0
prompts_path=
dry_run=0
check_only=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --container) instance="${2:?missing value for --container}"; shift 2 ;;
        --cloud) cloud=1; shift ;;
        --prompts) prompts_path="${2:?missing value for --prompts}"; shift 2 ;;
        --dry-run) dry_run=1; shift ;;
        --check) check_only=1; shift ;;
        -h|--help) usage; exit 0 ;;
        --*) usage; exit 2 ;;
        *)
            [[ -z "${instance}" ]] || { usage; exit 2; }
            instance="$1"
            shift
            ;;
    esac
done

[[ -n "${instance}" ]] || { usage; exit 2; }
(( dry_run + check_only <= 1 )) || {
    echo "ai-container-tools: --dry-run and --check are mutually exclusive" >&2
    exit 2
}
command -v incus >/dev/null 2>&1 || {
    echo "ai-container-tools: incus is not installed" >&2
    exit 1
}
incus info "${instance}" >/dev/null 2>&1 || {
    echo "ai-container-tools: Incus instance not found: ${instance}" >&2
    exit 1
}

guest_user="${AI_SANDBOX_USER:-${USER:?USER is not set}}"
guest_home="/home/${guest_user}"
prompts_path="${prompts_path:-${guest_home}/prompts}"
profile=local
if (( cloud == 1 )); then
    profile=cloud
fi

if (( dry_run == 1 )); then
    echo "[container:${instance}] would install/update: node LTS, opencode2, pi, dsh, slopshell"
    if (( cloud == 1 )); then
        echo "[container:${instance}] would install/update: Codex CLI, Claude Code"
    else
        echo "[container:${instance}] would not install cloud CLIs"
    fi
    echo "[container:${instance}] would wire read-only prompts at ${prompts_path}"
    echo "[container:${instance}] would not copy host credentials or modify host MCP registrations"
    exit 0
fi

# Everything below is sent over Incus stdin. Network downloads, package
# installs, and config writes therefore happen in the guest namespace.
incus exec "${instance}" -- bash -s -- \
    "${guest_user}" "${guest_home}" "${prompts_path}" "${profile}" \
    "$([[ ${check_only} -eq 1 ]] && echo check || echo install)" <<'GUEST'
set -euo pipefail
guest_user="$1"
guest_home="$2"
prompts_path="$3"
profile="$4"
mode="$5"

user_env=(HOME="$guest_home" USER="$guest_user" LOGNAME="$guest_user")
user_path="$guest_home/.local/bin:$guest_home/.opencode/bin:/usr/local/bin:/usr/bin:/bin"
as_user() {
    runuser -u "$guest_user" -- env "${user_env[@]}" PATH="$user_path" "$@"
}
check_tool() {
    local name="$1"
    as_user bash -lc "command -v '$name' >/dev/null 2>&1" || {
        echo "missing guest tool: $name" >&2
        return 1
    }
}

if [[ "$mode" == check ]]; then
    check_tool pi
    check_tool dsh
    (command -v opencode2 >/dev/null 2>&1 || command -v opencode >/dev/null 2>&1) || {
        echo "missing guest tool: opencode2/opencode" >&2
        exit 1
    }
    check_tool slopshell
    [[ -f "$prompts_path/AGENTS.md" ]] || {
        echo "prompts checkout is not mounted read-only: $prompts_path" >&2
        exit 1
    }
    if [[ "$profile" == cloud ]]; then
        check_tool codex
        check_tool claude
    fi
    echo "AI container tools OK ($profile)"
    exit 0
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git golang-go jq util-linux

# Keep Node in the guest user's home using the official nvm bootstrap.
as_user bash -lc '
    set -euo pipefail
    export NVM_DIR="$HOME/.nvm"
    if [[ ! -s "$NVM_DIR/nvm.sh" ]]; then
        curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
    fi
    . "$NVM_DIR/nvm.sh"
    nvm install --lts
    nvm alias default "$(nvm version lts/*)"
    nvm use default >/dev/null
    npm install --global --ignore-scripts @earendil-works/pi-coding-agent
    npm install --global @deepseek-ai/dsh@alpha
'

# Official installers run as the guest user, so their update state is private
# to this persistent container home.
as_user bash -lc 'curl -fsSL https://opencode.ai/v2/install | bash'
GOBIN=/usr/local/bin go install github.com/sloppy-org/slopshell/cmd/slopshell@latest

if [[ "$profile" == cloud ]]; then
    as_user bash -lc 'CODEX_NON_INTERACTIVE=1 sh -c "$(curl -fsSL https://chatgpt.com/codex/install.sh)"'
    as_user bash -lc 'curl -fsSL https://claude.ai/install.sh | bash'
fi

# The prompts checkout is an Incus read-only disk device. The installer links
# skills/rules/configuration into the guest home and preserves guest-local
# credentials; it receives none from the host.
as_user bash -lc "PATH=\"$user_path\" AI_CONTAINER_PROFILE=\"$profile\" '$prompts_path/scripts/install.sh'"

install -d -o "$guest_user" -g "$guest_user" -m 0755 "$guest_home/.local/bin"
ln -sfn /usr/local/bin/slopshell "$guest_home/.local/bin/slopshell"
chown -h "$guest_user:$guest_user" "$guest_home/.local/bin/slopshell"
echo "AI container tools installed ($profile)"
GUEST
