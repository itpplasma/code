#!/bin/bash

echo "Setting up GPEC..."
echo "Upstream code access: https://princetonuniversity.github.io/GPEC/developers.html"

export NETCDF_LIBS="-lnetcdf -lnetcdff"

if [[ $(module list 2>&1) == *"compiler/2025.0.0"* ]]; then
    # Intel compiler (recommended)
    export CC=icx
    export CXX=icpx
    export FC=ifx
    export MKLROOT=/opt/intel/oneapi/mkl/2025.0
    export OMPFLAG="-qopenmp"
    module load netcdf-fortran/4.6.1-intel
else
    # GNU compiler (experimental)
    export CC=gcc
    export CXX=g++
    export FC=gfortran
    export FFLAGS="-fdefault-real-8 -fdefault-double-8 -fallow-argument-mismatch"
    export LAPACK_HOME="/usr/include"
    export NETCDF_FORTRAN_HOME="/usr/include"
    export OMPFLAG="-fopenmp"
fi

pushd $CODE/external
    git clone -b master git@gitlab.tugraz.at:plasma/codes/gpec.git GPEC
    pushd GPEC/install
        make
    popd
popd
