#!/bin/bash

export FFLAGS=-fPIC
export CFLAGS=-fPIC
export CXXFLAGS=-fPIC

echo "Fetching and building HDF5..."
curl -L https://github.com/HDFGroup/hdf5/releases/download/hdf5-1_14_3/hdf5-1_14_3.tar.gz -o - | tar xz
mkdir -p hdfsrc/build
pushd hdfsrc/build
cmake -DCMAKE_INSTALL_PREFIX:PATH=$(pwd) -DBUILD_SHARED_LIBS=ON \
      -DHDF5_BUILD_FORTRAN=ON ..
make -j$(nproc) install
popd

echo "Fetching and building NetCDF..."
curl -L https://github.com/Unidata/netcdf-c/archive/refs/tags/v4.9.2.tar.gz -o - | tar xz
pushd netcdf-c-4.9.2
CPPFLAGS="-I$(pwd)/../hdfsrc/build/include" LDFLAGS="-L$(pwd)/../hdfsrc/build/lib" ./configure --prefix="$(pwd)/build"
make -j$(nproc)
make install
popd

curl -L https://github.com/Unidata/netcdf-fortran/archive/refs/tags/v4.6.1.tar.gz -o - | tar xz
mkdir -p netcdf-fortran-4.6.1/build
pushd netcdf-fortran-4.6.1/build
cmake -DCMAKE_INSTALL_PREFIX:PATH=$(pwd) -DNETCDF_C_LIBRARY=$(pwd)/../netcdf-c-4.9.2/build/lib/libnetcdf.so -DNETCDF_C_INCLUDE_DIR=$(pwd)/../netcdf-c-4.9.2/build/include ..
make -j$(nproc) install
popd
