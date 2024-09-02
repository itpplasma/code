#!/bin/bash

/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

(echo; echo 'eval "$(/usr/local/bin/brew shellenv)"') >> $HOME/.zprofile
eval "$(/usr/local/bin/brew shellenv)"

brew install --cask orbstack devpod visual-studio-code

sudo ln -s /Applications/OrbStack.app/Contents/MacOS/xbin/docker /usr/local/bin/docker

devpod provider add docker
devpod up git@gitlab.tugraz.at:plasma/code