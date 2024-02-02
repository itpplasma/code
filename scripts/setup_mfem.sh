#!/bin/bash

if [ ! -d "mfem-4.6" ] ; then
    echo "Fetching and building MFEM..."
    wget https://bit.ly/mfem-4-6 -O mfem-4.6.tgz
    tar xzvf mfem-4.6.tgz
    cd mfem-4.6
    mkdir build
    cd build
    cmake .. -DMFEM_USE_SUITESPARSE=1 -DCMAKE_CXX_FLAGS=-fPIC
    make -j4
    cd ../..
fi
