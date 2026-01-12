#!/usr/bin/env bash

install_git_lfs() {
    if ! command -v git-lfs &>/dev/null; then
        echo installing git-lfs...
        GIT_LFS_VERSION=$(curl -s "https://api.github.com/repos/git-lfs/git-lfs/releases/latest" | grep -Po '"tag_name": "v\K[^"]*')
        curl -Lo /tmp/git-lfs.tar.gz "https://github.com/git-lfs/git-lfs/releases/latest/download/git-lfs-linux-amd64-v${GIT_LFS_VERSION}.tar.gz"
        tar -xf /tmp/git-lfs.tar.gz -C /tmp
        mkdir -p $HOME/.local/bin
        install /tmp/git-lfs-${GIT_LFS_VERSION}/git-lfs $HOME/.local/bin
        rm -rf /tmp/git-lfs*
        git lfs install
        echo git-lfs installed
    else
        echo git-lfs already installed
    fi
}

install_git_lfs
