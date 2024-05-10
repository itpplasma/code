#!/bin/bash

if [ ! -d "scalapack" ] ; then
    echo "Fetching and building ScaLapack..."
    git clone https://github.com/Reference-ScaLAPACK/scalapack.git
    pushd scalapack
    cmake -S. -Bbuild -DBUILD_SHARED_LIBS=ON -DCMAKE_BUILD_TYPE=Release -DCMAKE_Fortran_FLAGS=-fPIC -DCMAKE_C_FLAGS=-fPIC -DCMAKE_CXX_FLAGS=-fPIC
    pushd build
    make -j$(nproc)
    popd
    popd
fi
