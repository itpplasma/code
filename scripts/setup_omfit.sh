#!/bin/bash

git clone https://github.com/gafusion/OMFIT-source.git
cd OMFIT-source
git submodule update --init --recursive
export OMAS_ROOT=$PWD/omas
export USER=`whoami`
python -m pip install -e .
