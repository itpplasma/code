#!/usr/bin/env bash
# Additional headless development packages shared by Debian and Ubuntu.
set -euo pipefail

required=(
    age autoconf automake bison clang direnv fd-find flex gdb gh
    jq krb5-user neovim nfs-common pipx podman procps ripgrep shellcheck
    sqlite3 sshuttle stow tmux tree valgrind vim zsh
)
optional=(
    fuse-overlayfs glab lldb podman-compose slirp4netns uidmap
)

apt-get install -y -q --no-install-recommends "${required[@]}"

# A few convenience packages vary between Debian and Ubuntu releases. Install
# those exposed by the current release without making the whole setup fail.
available=()
for package in "${optional[@]}"; do
    if apt-cache show "${package}" >/dev/null 2>&1; then
        available+=("${package}")
    else
        echo "optional apt package unavailable, skipping: ${package}"
    fi
done
if [[ ${#available[@]} -gt 0 ]]; then
    apt-get install -y -q --no-install-recommends "${available[@]}"
fi
