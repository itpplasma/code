#!/bin/bash

if [ ! -d "mfem-4.7" ] ; then
    echo "Fetching and building MFEM..."
    curl -L https://github.com/mfem/mfem/archive/refs/tags/v4.7.tar.gz -o - | tar xz
    mkdir -p mfem-4.7/build
    pushd mfem-4.7/build
    cd build
    cmake .. -DMFEM_USE_SUITESPARSE=1 -DCMAKE_CXX_FLAGS=-fPIC
    make -j8
    popd
fi
