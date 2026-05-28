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

# setup.sh clones libneo to the workspace ($CODE = /workspaces); stash it inside
# the infra template so postCreateCommand.sh can restore it as a sibling.
mv /workspaces/libneo /workspaces/code/libneo
mv /workspaces/code $CODE_TEMPLATE

rm -rf /workspaces
