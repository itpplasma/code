#!/bin/bash

set -e
source /etc/profile.d/modules.sh
module use -a $CODE/modules
module load mephit

pushd MEPHIT
    ../scripts/checkout_branch.sh $CODE_BRANCH
    mkdir build
    pushd build
        cmake ..
        make -j4
    popd
    pip install -e . --no-build-isolation
popd
