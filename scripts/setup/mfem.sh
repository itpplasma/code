#!/usr/bin/env bash

version=4.10
if [ ! -d "mfem-$version" ] ; then
    echo "Fetching and building MFEM..."
    curl -L "https://github.com/mfem/mfem/archive/refs/tags/v$version.tar.gz" -o - | tar xz
    mkdir -p "mfem-$version/build"
    pushd "mfem-$version/build"
    cmake .. -DMFEM_USE_SUITESPARSE=1 -DCMAKE_CXX_FLAGS=-fPIC
    make -j8
    popd
fi
