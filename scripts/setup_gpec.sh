#!/bin/bash

echo "Setting up GPEC..."
echo "Local use only at ITPcp, not suited for Docker container."
echo "Works with Intel compiler, not GNU or NVIDIA/PGI."
echo "Code access: https://princetonuniversity.github.io/GPEC/developers.html"

export FC=ifx
export CC=icx
export CXX=icx
export MKLROOT=/opt/intel/oneapi/mkl/2024.2

pushd $CODE/external
    module load intelcompiler/2024.2
    pushd intel
        source setup_netcdf.sh
    popd

    module netcdf-fortran/4.6.1-intel

    git clone -b master git@github.com:PrincetonUniversity/GPEC.git
    pushd GPEC/install
        OMPFLAG=-qopenmp make
    popd
popd
