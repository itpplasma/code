#!/bin/bash

if [ ! -d "mfem-4.6.2-rc0" ] ; then
    echo "Fetching and building MFEM..."
    wget https://github.com/mfem/mfem/archive/refs/tags/v4.6.2-rc0.tar.gz -O mfem-4.6.2-rc0.tgz
    tar xzvf mfem-4.6.2-rc0.tgz
    mkdir -p mfem-4.6.2-rc0/build
    pushd mfem-4.6.2-rc0/build
    cd build
    cmake .. -DMFEM_USE_SUITESPARSE=1 -DCMAKE_CXX_FLAGS=-fPIC
    make -j8
    popd
fi
