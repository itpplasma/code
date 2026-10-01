#!/usr/bin/env bash
# Install/update an unprivileged Node LTS toolchain with nvm.
set -euo pipefail

NVM_VERSION="${NVM_VERSION:-v0.40.3}"
NODE_VERSION="${NODE_VERSION:-22}"
export NVM_DIR="${NVM_DIR:-${HOME}/.nvm}"

if [[ ! -s "${NVM_DIR}/nvm.sh" ]]; then
    curl -fsSL "https://raw.githubusercontent.com/nvm-sh/nvm/${NVM_VERSION}/install.sh" | bash
fi
# shellcheck disable=SC1091
. "${NVM_DIR}/nvm.sh"
nvm install "${NODE_VERSION}"
nvm alias default "${NODE_VERSION}"
nvm use default
node --version
npm --version
