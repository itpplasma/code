#/bin/bash

export GIT_HTTPS=1
export CODE_TEMPLATE=/usr/local/src/code_template
export CODE=/workspaces/code

mkdir -p /workspaces
mkdir -p /usr/local/src

cd /workspaces
git clone https://github.com/itpplasma/code.git code

cd $CODE
source scripts/setup.sh

mv $CODE $CODE_TEMPLATE
