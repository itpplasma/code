#!/bin/bash

echo "Setting up GPEC..."
echo "Upstream code access: https://princetonuniversity.github.io/GPEC/developers.html"

pushd $CODE/external
    git clone -b master git@gitlab.tugraz.at:plasma/codes/gpec.git GPEC
    export FFLAGS="-fdefault-real-8 -fdefault-double-8 -fallow-argument-mismatch"
    export LAPACK_HOME="/usr/include"
    export NETCDF_FORTRAN_HOME="/usr/include"
    export NETCDF_LIBS="-lnetcdf -lnetcdff"
    pushd GPEC/install
        OMPFLAG=-fopenmp make
    popd
popd
