#!/bin/bash

/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

(echo; echo 'eval "$(/usr/local/bin/brew shellenv)"') >> $HOME/.zprofile
eval "$(/usr/local/bin/brew shellenv)"

brew install --cask orbstack
brew install --cask devpod
brew install --cask visual-studio-code