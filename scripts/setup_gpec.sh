#!/bin/bash

echo "Setting up GPEC..."
echo "Local use only at ITPcp, not suited for Docker container."
echo "Works with Intel compiler, not GNU or NVIDIA/PGI."
echo "Upstream code access: https://princetonuniversity.github.io/GPEC/developers.html"

module load intel/compiler/2025.0.0 intel/mkl/2025.0
export FC=ifx
export CC=icx
export CXX=icx
export MKLROOT=/opt/intel/oneapi/mkl/2025.0

pushd $CODE/external
    pushd intel
        source setup_netcdf.sh
    popd

    module load netcdf-fortran/4.6.1-intel

    git clone -b master git@gitlab.tugraz.at:plasma/codes/gpec.git
    pushd GPEC/install
        OMPFLAG=-qopenmp make
    popd
popd
