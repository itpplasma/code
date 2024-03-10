#!/bin/bash

echo "Building and installing 'efit_to_boozer'..."
if [ ! -d "efit_to_boozer" ] ; then
    echo "Cloning 'efit_to_boozer'..."
    clone_github efit_to_boozer
fi
cd efit_to_boozer
../scripts/checkout_branch.sh $CI_MERGE_REQUEST_SOURCE_BRANCH_NAME
pip install -e .
cd ..

echo "Building and installing 'libneo'..."
if [ ! -d "libneo" ] ; then
    echo "Cloning 'libneo'..."
    clone_github libneo
fi
cd libneo
../scripts/checkout_branch.sh $CI_MERGE_REQUEST_SOURCE_BRANCH_NAME
pip install -e .
cd ..
