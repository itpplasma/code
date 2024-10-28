#!/bin/bash

export FFLAGS=-fPIC
export CFLAGS=-fPIC
export CXXFLAGS=-fPIC

echo "Fetching and building HDF5..."
curl -L https://github.com/HDFGroup/hdf5/releases/download/hdf5_1.14.5/hdf5-1.14.5.tar.gz -o - | tar xz
mkdir -p hdf5-1.14.5/build
pushd hdf5-1.14.5
      sed -i 's/\${CONTENTS}/"\${CONTENTS}"/' config/cmake/HDFUseFortran.cmake
popd
pushd hdf5-1.14.5/build
      cmake -DCMAKE_INSTALL_PREFIX:PATH=$(pwd) -DBUILD_SHARED_LIBS=ON \
            -DHDF5_BUILD_FORTRAN=ON ..
      make install
popd

echo "Fetching and building NetCDF..."
curl -L https://github.com/Unidata/netcdf-c/archive/refs/tags/v4.9.2.tar.gz -o - | tar xz
pushd netcdf-c-4.9.2
      CPPFLAGS="-I$(pwd)/../hdf5-1.14.5/build/include" \
      LDFLAGS="-L$(pwd)/../hdf5-1.14.5/build/lib" \
      ./configure --prefix="$(pwd)/build"
      make install
popd

curl -L https://github.com/Unidata/netcdf-fortran/archive/refs/tags/v4.6.1.tar.gz -o - | tar xz
mkdir -p netcdf-fortran-4.6.1/build
pushd netcdf-fortran-4.6.1/build
      cmake -DCMAKE_INSTALL_PREFIX:PATH=$(pwd) \
            -DNETCDF_C_LIBRARY=$(pwd)/../netcdf-c-4.9.2/build/lib/libnetcdf.so \
            -DNETCDF_C_INCLUDE_DIR=$(pwd)/../netcdf-c-4.9.2/build/include ..
      make install
popd
