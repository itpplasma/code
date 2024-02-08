#!/bin/bash

echo "Setting up OMFIT..."
echo "Local use only at ITPcp, not suited for Docker container."
echo "Requires NVIDIA/PGI compiler, not GNU or Intel."
echo "Code access: https://omfit.io/install.html"

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

git clone git@github.com:gafusion/OMFIT-source.git
cd OMFIT-source
git submodule update --init --recursive
export OMAS_ROOT=$PWD/omas
export USER=`whoami`
python -m pip install -e .

mkdir -p $HOME/.LICENSES
cp $DIR/OMFIT/LICENSES/* $HOME/.LICENSES/
