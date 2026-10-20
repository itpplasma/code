#!/usr/bin/env bash

export GIT_HTTPS=1
export CODE_TEMPLATE=/usr/local/src/code_template

mkdir -p /workspaces
mkdir -p /usr/local/src

# infra repo at /workspaces/code, code checkouts as siblings under /workspaces
cd /workspaces
git clone https://github.com/itpplasma/code.git code

cd /workspaces/code
source scripts/setup.sh

# setup.sh puts libneo and external in the workspace ($CODE = /workspaces);
# stash them inside the infra template so postCreateCommand.sh can restore them.
mv /workspaces/libneo /workspaces/code/libneo
mv /workspaces/external /workspaces/code/external
mv /workspaces/code $CODE_TEMPLATE

rm -rf /workspaces
