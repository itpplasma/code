#!/bin/bash

set -e
source /etc/profile.d/modules.sh
module use -a $CODE/modules
module load mephit

pushd MEPHIT
    ../scripts/checkout_branch.sh $CODE_BRANCH
    mkdir build
    pushd build
        cmake .. $CMAKE_ARGS
        make -j4
    popd
    pip install -e .
popd
