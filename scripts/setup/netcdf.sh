#!/usr/bin/env bash

cd "$CODE/external" || exit 1

HDF5_VERSION=1.14.5
NETCDF_C_VERSION=4.9.2
NETCDF_F_VERSION=4.6.1

INSTALL_PREFIX="$CODE/external/netcdf-install"

# Check if system NetCDF-Fortran is available
if command -v nf-config &> /dev/null; then
    echo "System NetCDF-Fortran found: $(nf-config --version)"
    exit 0
fi

# Check if already built
if [ -f "$INSTALL_PREFIX/lib/libnetcdff.so" ]; then
    echo "NetCDF already built in $INSTALL_PREFIX"
    exit 0
fi

export FFLAGS=-fPIC
export CFLAGS=-fPIC
export CXXFLAGS=-fPIC

mkdir -p "$INSTALL_PREFIX"

# Build HDF5
if [ ! -f "$INSTALL_PREFIX/lib/libhdf5.so" ]; then
    echo "Fetching and building HDF5 ${HDF5_VERSION}..."
    if [ ! -d "hdf5-${HDF5_VERSION}" ]; then
        curl -L https://github.com/HDFGroup/hdf5/releases/download/hdf5_${HDF5_VERSION}/hdf5-${HDF5_VERSION}.tar.gz -o - | tar xz
        # Fix quoting issue in HDF5 CMake
        sed -i 's/\${CONTENTS}/"\${CONTENTS}"/' hdf5-${HDF5_VERSION}/config/cmake/HDFUseFortran.cmake
    fi
    mkdir -p hdf5-${HDF5_VERSION}/build
    cd hdf5-${HDF5_VERSION}/build
    cmake -DCMAKE_INSTALL_PREFIX:PATH="$INSTALL_PREFIX" \
          -DBUILD_SHARED_LIBS=ON \
          -DHDF5_BUILD_FORTRAN=ON ..
    make -j$(nproc) install
    cd "$CODE/external"
fi

# Build NetCDF-C
if [ ! -f "$INSTALL_PREFIX/lib/libnetcdf.so" ]; then
    echo "Fetching and building NetCDF-C ${NETCDF_C_VERSION}..."
    if [ ! -d "netcdf-c-${NETCDF_C_VERSION}" ]; then
        curl -L https://github.com/Unidata/netcdf-c/archive/refs/tags/v${NETCDF_C_VERSION}.tar.gz -o - | tar xz
    fi
    cd netcdf-c-${NETCDF_C_VERSION}
    CPPFLAGS="-I$INSTALL_PREFIX/include" \
    LDFLAGS="-L$INSTALL_PREFIX/lib -Wl,-rpath,$INSTALL_PREFIX/lib" \
    ./configure --prefix="$INSTALL_PREFIX" --disable-libxml2 --disable-byterange
    make -j$(nproc) install
    cd "$CODE/external"
fi

# Build NetCDF-Fortran
if [ ! -f "$INSTALL_PREFIX/lib/libnetcdff.so" ]; then
    echo "Fetching and building NetCDF-Fortran ${NETCDF_F_VERSION}..."
    if [ ! -d "netcdf-fortran-${NETCDF_F_VERSION}" ]; then
        curl -L https://github.com/Unidata/netcdf-fortran/archive/refs/tags/v${NETCDF_F_VERSION}.tar.gz -o - | tar xz
    fi
    mkdir -p netcdf-fortran-${NETCDF_F_VERSION}/build
    cd netcdf-fortran-${NETCDF_F_VERSION}/build
    cmake -DCMAKE_INSTALL_PREFIX:PATH="$INSTALL_PREFIX" \
          -DNETCDF_C_LIBRARY="$INSTALL_PREFIX/lib/libnetcdf.so" \
          -DNETCDF_C_INCLUDE_DIR="$INSTALL_PREFIX/include" \
          -DCMAKE_PREFIX_PATH="$INSTALL_PREFIX" ..
    make -j$(nproc) install
    cd "$CODE/external"
fi

echo "NetCDF stack installed to $INSTALL_PREFIX"
