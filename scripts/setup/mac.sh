#!/usr/bin/env bash

/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

(echo; echo 'eval "$(/usr/local/bin/brew shellenv)"') >> $HOME/.zprofile
eval "$(/usr/local/bin/brew shellenv)"

brew install gcc make cmake ninja python@3.11 python-tk@3.11
brew install gsed autoconf automake autogen git lazygit
brew install openblas suitesparse metis
brew install hdf5 netcdf netcdf-fortran fftw graphviz
brew install --cask orbstack devpod visual-studio-code

sudo ln -s /Applications/OrbStack.app/Contents/MacOS/xbin/docker /usr/local/bin/docker

mkdir -p $HOME/Nextcloud/plasma
ln -s $HOME/.devpod/agent/contexts/default/workspaces/code/content $HOME/code

docker pull ghcr.io/itpplasma/devcontainer:latest

devpod provider add docker
devpod up git@github.com:itpplasma/code --id code --ide vscode
