#!/bin/bash

CODE_TEMPLATE=/usr/local/src/code_template
CODE=/workspaces/code

ln -s $CODE_TEMPLATE/.venv $CODE/.venv
ln -s $CODE_TEMPLATE/external/fgsl-1.6.0 $CODE/external/fgsl-1.6.0
cp -r $CODE_TEMPLATE/libneo $CODE/libneo

echo 'source /workspaces/code/activate.sh' >> $HOME/.bashrc
git config --global core.editor 'code --wait'
