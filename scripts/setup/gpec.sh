#!/usr/bin/env bash

echo "Setting up GPEC..."
echo "Upstream code access: https://princetonuniversity.github.io/GPEC/developers.html"

module load intel/compiler intel/ifort intel/mkl netcdf-fortran/4.6.1-intel

export NETCDF_LIBS="-lnetcdf -lnetcdff"
export CC=icx
export CXX=icpx
export FC=ifort
export OMPFLAG="-qopenmp"
cd $CODE/external/intel

git clone -b master git@gitlab.tugraz.at:plasma/codes/gpec.git GPEC
pushd GPEC/install
    make NETCDFLIBS="-lnetcdff -lnetcdf"
popd
