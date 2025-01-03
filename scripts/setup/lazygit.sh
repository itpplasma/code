#!/bin/bash

install_lazygit() {
    if ! command -v lazygit &>/dev/null; then
        echo installing Lazygit...
        LAZYGIT_VERSION=$(curl -s "https://api.github.com/repos/jesseduffield/lazygit/releases/latest" | grep -Po '"tag_name": "v\K[^"]*')
        curl -Lo lazygit.tar.gz --output-dir /tmp "https://github.com/jesseduffield/lazygit/releases/latest/download/lazygit_${LAZYGIT_VERSION}_Linux_x86_64.tar.gz"
        tar -xf /tmp/lazygit.tar.gz -C /tmp lazygit
	mkdir -p $HOME/.local/bin
        install /tmp/lazygit $HOME/.local/bin
        rm /tmp/lazygit*
        echo Lazygit installed
    else
        echo Lazygit already installed
    fi
}

install_lazygit
