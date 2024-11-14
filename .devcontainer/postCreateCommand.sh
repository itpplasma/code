#/bin/bash

export GIT_HTTPS=1
source scripts/setup.sh
echo 'source /workspaces/code/activate.sh' >> $HOME/.bashrc
git config --global core.editor 'code --wait'
