#!/bin/bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

git clone https://github.com/gafusion/OMFIT-source.git
cd OMFIT-source
git submodule update --init --recursive
export OMAS_ROOT=$PWD/omas
export USER=`whoami`
python -m pip install -e .

mkdir -p $HOME/.LICENSES
cp $DIR/OMFIT/LICENSES/* $HOME/.LICENSES/
