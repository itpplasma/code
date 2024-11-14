#/bin/bash

export CODE="/workspaces/code"
cd $CODE

source scripts/setup.sh
echo 'source /workspaces/code/activate.sh' >> $HOME/.bashrc
git config --global core.editor 'code --wait'
