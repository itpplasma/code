#!/usr/bin/env bash
# Install or update user-scoped AI coding CLIs. Safe to re-run.
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)"
mkdir -p "${HOME}/.local/bin" "${HOME}/.config/ai-infra" "${HOME}/workspace"

ensure_profile_line() {
    local line="$1" file="${HOME}/.profile"
    touch "${file}"
    grep -Fqx "${line}" "${file}" || printf '%s\n' "${line}" >> "${file}"
}

# shellcheck disable=SC2016
ensure_profile_line 'export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$HOME/.local/go/bin:$HOME/go/bin:$PATH"'
export PATH="${HOME}/.local/bin:${HOME}/.opencode/bin:${HOME}/.local/go/bin:${HOME}/go/bin:${PATH}"

bash "${SCRIPT_DIR}/nodejs.sh"
bash "${SCRIPT_DIR}/go.sh"
bash "${SCRIPT_DIR}/glab.sh"
export NVM_DIR="${HOME}/.nvm"
# shellcheck disable=SC1091
. "${NVM_DIR}/nvm.sh"
nvm use default >/dev/null

# Official installers are intentionally re-run: each is also the supported
# update path and keeps architecture selection out of this repository.
CODEX_NON_INTERACTIVE=1 sh -c "$(curl -fsSL https://chatgpt.com/codex/install.sh)"
bash "${SCRIPT_DIR}/claude.sh"
curl -fsSL https://opencode.ai/v2/install | bash
curl -LsSf https://astral.sh/uv/install.sh | sh
sh -c "$(curl -fsLS https://get.chezmoi.io)" -- -b "${HOME}/.local/bin"

npm uninstall --global @mariozechner/pi-coding-agent >/dev/null 2>&1 || true
npm install --global --ignore-scripts @earendil-works/pi-coding-agent

# NVM's bin directory is initialized late in interactive .bashrc files. Keep a
# stable entry point so Pi also works through Multipass exec and automation.
cat > "${HOME}/.local/bin/pi" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export NVM_DIR="${HOME}/.nvm"
# shellcheck disable=SC1091
. "${NVM_DIR}/nvm.sh"
nvm use default >/dev/null
exec "${NVM_BIN}/pi" "$@"
EOF
chmod 0755 "${HOME}/.local/bin/pi"

# Codex keeps normal daily OAuth sessions persistently. On a desktop with a
# keyring, "auto" uses it; on a headless VM it falls back to auth.json.
mkdir -p "${HOME}/.codex"
touch "${HOME}/.codex/config.toml"
if ! grep -Eq '^[[:space:]]*cli_auth_credentials_store[[:space:]]*=' "${HOME}/.codex/config.toml"; then
    printf '\ncli_auth_credentials_store = "auto"\n' >> "${HOME}/.codex/config.toml"
fi

cat > "${HOME}/.config/ai-infra/env.example" <<'EOF'
# Copy to ~/.config/ai-infra/env, chmod 600, and source only when needed.
# Provider logins stored by codex/claude/opencode/pi do not belong here.
# ANTHROPIC_API_KEY=
# OPENAI_API_KEY=
EOF

echo "AI CLIs installed. Log in normally; their revocable sessions persist in this VM."
echo "After 'gh auth login', run: ai-private-tools"
