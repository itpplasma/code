#/bin/bash

export GIT_HTTPS=1
export CODE=/workspaces/code

mkdir -p /workspaces
cd /workspaces
git clone https://github.com/itpplasma/code.git code

cd $CODE
source scripts/setup.sh
echo 'source /workspaces/code/activate.sh' >> $HOME/.bashrc
git config --global core.editor 'code --wait'
