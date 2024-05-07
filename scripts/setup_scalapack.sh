#!/bin/bash

if [ ! -d "scalapack" ] ; then
    echo "Fetching and building ScaLapack..."
    git clone https://github.com/Reference-ScaLAPACK/scalapack.git
    pushd scalapack
    cmake -S. -Bbuild
    pushd build
    make
    popd
    popd
fi
