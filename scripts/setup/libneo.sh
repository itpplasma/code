#!/usr/bin/env bash

set -e

source $INFRA/scripts/util.sh
set_branch

echo "Building and installing 'libneo'..."
if [ ! -d "libneo" ] ; then
    echo "Cloning 'libneo'..."
    clone_github libneo
fi
pushd libneo
$INFRA/scripts/checkout_branch.sh $CODE_BRANCH
pip install --verbose --no-build-isolation -e .
popd
