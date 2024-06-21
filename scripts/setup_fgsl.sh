#!/bin/bash

if [ ! -d "fgsl-1.6.0" ] ; then
    echo "Fetching and building FGSL..."
    curl -L https://github.com/reinh-bader/fgsl/archive/refs/tags/v1.6.0.tar.gz -o - | tar xzv
    pushd fgsl-1.6.0
    mkdir m4
    autoreconf -i
    ./configure
    make
    popd
fi
