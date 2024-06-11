#!/bin/bash

echo "Building and installing NEO-RT..."
if [ ! -d "contrib" ] ; then
    mkdir contrib
fi
pushd contrib
if [ ! -d "quadpack" ] ; then
    clone_github quadpack
fi
if [ ! -d "vode" ] ; then
    clone_github vode
fi
popd
if [ ! -d "spline" ] ; then
    clone_github spline
fi
if [ ! -d "BOOZER_MAGFIE" ] ; then
    clone_github BOOZER_MAGFIE
fi
if [ ! -d "NEO-RT" ] ; then
    clone_github NEO-RT
fi
pushd NEO-RT
$CODE/scripts/checkout_branch.sh $CODE_BRANCH
if [ -d "build" ] ; then
    rm -rf build
fi
mkdir build
pushd build
cmake ..
make
popd
popd