#!/bin/bash

echo "Setting up OMFIT..."
echo "Requires personal Git access to OMFIT repository."
echo "Code access: https://omfit.io/install.html"

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

deactivate
curl https://pyenv.run | bash
pyenv install 3.7.17

git clone --filter=blob:none git@github.com:gafusion/OMFIT-source.git
pushd OMFIT-source
pyenv local 3.7.17
git submodule update --init --recursive
export OMAS_ROOT=$PWD/omas
export USER=`whoami`
python -m pip install -r install/requirements.txt
python -m pip install -e .
mkdir -p $HOME/.LICENSES
cp $DIR/OMFIT/LICENSES/* $HOME/.LICENSES/
popd
