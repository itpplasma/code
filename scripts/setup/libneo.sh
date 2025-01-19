#!/bin/bash

set -e

source $CODE/scripts/util.sh

echo "Building and installing 'libneo'..."
if [ ! -d "libneo" ] ; then
    echo "Cloning 'libneo'..."
    clone_github libneo
fi
pushd libneo
$CODE/scripts/checkout_branch.sh $CODE_BRANCH
pip install --verbose --no-build-isolation -e .
popd
