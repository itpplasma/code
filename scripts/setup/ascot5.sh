#!/usr/bin/env bash

set -e

source $CODE/scripts/util.sh

echo "Setting up ASCOT5..."

mkdir -p external
pushd external

if [ ! -d "ascot5" ]; then
    echo "Cloning ascot5 from itpplasma fork..."
    clone_github ascot5
    cd ascot5
    echo "Adding upstream remote..."
    git remote add upstream git@github.com:ascot4fusion/ascot5.git
    git fetch upstream
    cd ..
fi

pushd ascot5

echo "Remotes configured:"
git remote -v

echo "Building ascot5_main with release flags..."
make -j$(nproc) ascot5_main

echo "Building libascot..."
make -j$(nproc) libascot

echo ""
echo "Build complete. Binaries in: $(pwd)/build/"
ls -la build/

popd
popd
