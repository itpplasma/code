#!/usr/bin/env bash
# Compatibility entry point for the traditional full Debian workstation.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ${EUID} -eq 0 ]]; then
    exec "${SCRIPT_DIR}/apt.sh" full "$@"
else
    exec sudo "${SCRIPT_DIR}/apt.sh" full "$@"
fi
