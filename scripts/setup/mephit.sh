#!/usr/bin/env bash

set -e
source /etc/profile.d/modules.sh
module use -a $INFRA/modules
module load mephit

pushd MEPHIT
    $INFRA/scripts/checkout_branch.sh $CODE_BRANCH
    make
    pip install -e .
popd
