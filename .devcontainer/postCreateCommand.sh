#!/bin/bash

CODE_TEMPLATE=/usr/local/src/code_template
INFRA=/workspaces/code
CODE=/workspaces

# .venv is infra-private; external and libneo are workspace-shared
ln -s $CODE_TEMPLATE/.venv $INFRA/.venv
mkdir -p $CODE/external
ln -s $CODE_TEMPLATE/external/fgsl-1.6.0 $CODE/external/fgsl-1.6.0
cp -r $CODE_TEMPLATE/libneo $CODE/libneo

echo 'source /workspaces/code/activate.sh' >> $HOME/.bashrc
git config --global core.editor 'code --wait'
