#!/bin/bash

set -e
source /etc/profile.d/modules.sh
module use -a $CODE/modules
module load mephit

pushd external
    ../scripts/setup_triangle.sh
popd

pushd MEPHIT
    ../scripts/checkout_branch.sh $CODE_BRANCH
    make
    pip install -e .
popd
