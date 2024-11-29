#!/bin/bash

echo "Fetching and building Triangle..."
clone_github triangle
mkdir -p triangle/build
pushd triangle/build
    cmake ..
    make
popd
