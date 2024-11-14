#!/bin/bash

CODE_TEMPLATE=/usr/local/src/code_template
CODE=/workspaces/code

mv $CODE_TEMPLATE/.venv $CODE
mv $CODE_TEMPLATE/efit_to_boozer $CODE
mv $CODE_TEMPLATE/libneo $CODE
mv $CODE_TEMPLATE/external/fgsl-1.6.0 $CODE/external

echo 'source /workspaces/code/activate.sh' >> $HOME/.bashrc
git config --global core.editor 'code --wait'
