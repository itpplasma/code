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
  --mcp-socket PATH host-broker socket path as seen inside the container
  --dry-run         print guest operations without executing them
  --check           verify installed tools and prompt wiring without changes
EOF
}

instance=
cloud=0
prompts_path=
mcp_socket=${AI_MCP_SOCKET:-/run/sloppy.sock}
dry_run=0
check_only=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --container) instance="${2:?missing value for --container}"; shift 2 ;;
        --cloud) cloud=1; shift ;;
        --prompts) prompts_path="${2:?missing value for --prompts}"; shift 2 ;;
        --mcp-socket) mcp_socket="${2:?missing value for --mcp-socket}"; shift 2 ;;
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

# Build the checked-out Slopshell gateway on the host and copy only the
# resulting executable into the guest.  Installing `@latest` here could race
# the profile policy that is being tested and would also make deployment use a
# different revision from this checkout.
SLOPSHELL_SOURCE=${SLOPSHELL_SOURCE:-/home/ert/code/sloppy/slopshell}
slopshell_host_bin=""
slopshell_guest_path="/tmp/slopshell-profile-${instance//[^A-Za-z0-9_.-]/_}-$$"
if [[ -d "${SLOPSHELL_SOURCE}/.git" && ${dry_run} -eq 0 && ${check_only} -eq 0 ]]; then
    command -v go >/dev/null 2>&1 || {
        echo "ai-container-tools: Go is required to build ${SLOPSHELL_SOURCE}" >&2
        exit 1
    }
    slopshell_host_bin="$(mktemp)"
    trap 'rm -f "${slopshell_host_bin}"' EXIT
    (cd "${SLOPSHELL_SOURCE}" && go build -o "${slopshell_host_bin}" ./cmd/slopshell)
    chmod 0644 "${slopshell_host_bin}"
fi
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
    echo "[container:${instance}] would use MCP broker socket ${mcp_socket}"
    echo "[container:${instance}] would not copy host credentials or modify host MCP registrations"
    exit 0
fi

if [[ -n "${slopshell_host_bin}" ]]; then
    incus file push --mode 0644 "${slopshell_host_bin}" "${instance}${slopshell_guest_path}"
fi

guest_proxy_port=3128
[[ "${profile}" == local ]] && guest_proxy_port=3129
guest_proxy_url="${AI_EGRESS_PROXY_URL:-http://127.0.0.1:${guest_proxy_port}}"

# Everything below is sent over Incus stdin. Network downloads, package
# installs, and config writes therefore happen in the guest namespace.
incus exec "${instance}" -- bash -s -- \
    "${guest_user}" "${guest_home}" "${prompts_path}" "${profile}" "${mcp_socket}" "${slopshell_guest_path}" \
    "$([[ ${check_only} -eq 1 ]] && echo check || echo install)" "${guest_proxy_url}" <<'GUEST'
set -euo pipefail
guest_user="$1"
guest_home="$2"
prompts_path="$3"
profile="$4"
mcp_socket="$5"
slopshell_guest_path="$6"
mode="$7"
proxy_url="$8"

# Package and official-tool installers run as root or as a non-login shell;
# make their public downloads use the loopback Incus proxy explicitly.
export HTTP_PROXY="$proxy_url"
export HTTPS_PROXY="$proxy_url"
export http_proxy="$HTTP_PROXY"
export https_proxy="$HTTPS_PROXY"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost,::1}"
export no_proxy="$NO_PROXY"

user_env=(HOME="$guest_home" USER="$guest_user" LOGNAME="$guest_user")
user_path="$guest_home/.local/bin:$guest_home/.opencode/bin:/usr/local/bin:/usr/bin:/bin"
as_user() {
    runuser -u "$guest_user" -- env "${user_env[@]}" PATH="$user_path" "$@"
}
check_tool() {
    local name="$1"
    as_user bash -c "command -v '$name' >/dev/null 2>&1" || {
        echo "missing guest tool: $name" >&2
        return 1
    }
}

if [[ "$mode" == check ]]; then
    check_tool pi
    check_tool dsh
    as_user bash -c 'command -v opencode2 >/dev/null 2>&1 || command -v opencode >/dev/null 2>&1' || {
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
apt-get install -y --no-install-recommends ca-certificates curl git jq python3 util-linux

# Interactive shells inherit the same host-enforced proxy as direct `ai`
# commands.  The nft policy still drops all non-proxy forwarding.
install -d -m 0755 /etc/profile.d
printf '%s\n' \
    "export HTTP_PROXY=\"$proxy_url\"" \
    "export HTTPS_PROXY=\"$proxy_url\"" \
    'export http_proxy="$HTTP_PROXY"' \
    'export https_proxy="$HTTPS_PROXY"' \
    'export NO_PROXY="127.0.0.1,localhost,::1"' \
    'export no_proxy="$NO_PROXY"' \
    > /etc/profile.d/ai-egress-proxy.sh
chmod 0644 /etc/profile.d/ai-egress-proxy.sh

# Keep Node in the guest user's home using the official nvm bootstrap.
as_user bash -c '
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
    node_bin="$(dirname "$(command -v node)")"
    mkdir -p "$HOME/.local/bin"
    for tool in node npm npx pi; do
        if [[ -x "$node_bin/$tool" ]]; then
            ln -sfn "$node_bin/$tool" "$HOME/.local/bin/$tool"
        fi
    done
'

# Official installers run as the guest user, so their update state is private
# to this persistent container home.
as_user bash -c 'curl -fsSL https://opencode.ai/v2/install | bash'
install -d -o "$guest_user" -g "$guest_user" -m 0755 "$guest_home/.local/bin"
if [[ -x "$guest_home/.opencode/bin/opencode" ]]; then
    ln -sfn "$guest_home/.opencode/bin/opencode" "$guest_home/.local/bin/opencode"
    ln -sfn "$guest_home/.opencode/bin/opencode" "$guest_home/.local/bin/opencode2"
    chown -h "$guest_user:$guest_user" "$guest_home/.local/bin/opencode" "$guest_home/.local/bin/opencode2"
fi
if [[ -f "$slopshell_guest_path" ]]; then
    install -m 0755 "$slopshell_guest_path" /usr/local/bin/slopshell
    rm -f "$slopshell_guest_path"
else
    echo "missing profile-pinned Slopshell binary" >&2
    exit 1
fi
install -d -o "$guest_user" -g "$guest_user" -m 0755 "$guest_home/.local/bin"
ln -sfn /usr/local/bin/slopshell "$guest_home/.local/bin/slopshell"
chown -h "$guest_user:$guest_user" "$guest_home/.local/bin/slopshell"
cat > /usr/local/bin/slopshell-mcp-client <<'EOF'
#!/bin/sh
export SLOPSHELL_MCP_NO_HALF_CLOSE=1
exec /usr/local/bin/slopshell mcp-client "$@"
EOF
chmod 0755 /usr/local/bin/slopshell-mcp-client

if [[ "$profile" == cloud ]]; then
    as_user bash -c 'cd "$HOME"; CODEX_NON_INTERACTIVE=1 sh -c "$(curl -fsSL https://chatgpt.com/codex/install.sh)"'
    as_user bash -c 'cd "$HOME"; curl -fsSL https://claude.ai/install.sh | bash'
fi

# Install/update DSH through the prompts repository so its provider settings
# and script permissions stay in sync with the pinned prompts revision.
as_user bash -c "PATH=\"$user_path\" SLOPSHELL_CAPABILITY_PROFILE=\"$profile\" '$prompts_path/scripts/dsh-install.sh' --update --force-settings"
as_user bash -c '
    dsh_path="$(find "$HOME/.nvm/versions/node" -path "*/bin/dsh" -print -quit 2>/dev/null || true)"
    if [[ -n "$dsh_path" ]]; then ln -sfn "$dsh_path" "$HOME/.local/bin/dsh"; fi
'

# The prompts checkout is an Incus read-only disk device. The installer links
# skills/rules/configuration into the guest home and preserves guest-local
# credentials; it receives none from the host.
as_user bash -c "PATH=\"$user_path\" SLOPSHELL_CAPABILITY_PROFILE=\"$profile\" SLOPSHELL_MCP_COMMAND=\"/usr/local/bin/slopshell-mcp-client --socket $mcp_socket\" '$prompts_path/scripts/install.sh'"
echo "AI container tools installed ($profile)"
GUEST
