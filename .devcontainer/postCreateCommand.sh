#!/bin/bash

CODE_TEMPLATE=/usr/local/src/code_template
INFRA=/workspaces/code
CODE=/workspaces

# .venv and external belong to the infra repo; libneo is a sibling checkout
ln -s $CODE_TEMPLATE/.venv $INFRA/.venv
ln -s $CODE_TEMPLATE/external/fgsl-1.6.0 $INFRA/external/fgsl-1.6.0
cp -r $CODE_TEMPLATE/libneo $CODE/libneo

echo 'source /workspaces/code/activate.sh' >> $HOME/.bashrc
git config --global core.editor 'code --wait'
