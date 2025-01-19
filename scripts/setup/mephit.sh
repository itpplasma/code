#!/usr/bin/env bash

set -e
source /etc/profile.d/modules.sh
module use -a $CODE/modules
module load mephit

pushd external
    $CODE/scripts/setup/triangle.sh
popd

pushd MEPHIT
    $CODE/scripts/checkout_branch.sh $CODE_BRANCH
    make
    pip install -e .
popd
