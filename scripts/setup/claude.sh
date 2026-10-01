#!/usr/bin/env bash
# Anthropic's native installer auto-updates Claude Code in the background.
set -euo pipefail
curl -fsSL https://claude.ai/install.sh | bash
