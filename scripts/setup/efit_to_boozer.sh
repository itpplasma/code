#!/usr/bin/env bash

set -e

source $CODE/scripts/util.sh
set_branch

echo "Building and installing 'efit_to_boozer'..."
if [ ! -d "efit_to_boozer" ] ; then
    echo "Cloning 'efit_to_boozer'..."
    clone_github efit_to_boozer
fi
pushd efit_to_boozer
$CODE/scripts/checkout_branch.sh $CODE_BRANCH
pip install --verbose --no-build-isolation -e .
popd
