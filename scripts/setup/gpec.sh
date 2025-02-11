#!/usr/bin/env bash

echo "Setting up GPEC..."
echo "Upstream code access: https://princetonuniversity.github.io/GPEC/developers.html"

export NETCDF_LIBS="-lnetcdf -lnetcdff"

if [[ $(module list 2>&1) == *"intel/compiler"* ]]; then
    # Intel compiler (recommended)
    export CC=icc
    export CXX=icpc
    export FC=ifort
    export MKLROOT=/afs/itp.tugraz.at/opt/intel/2018.1/mkl
    export OMPFLAG="-qopenmp"
    module load netcdf-fortran/4.6.1-intel
else
    # GNU compiler (experimental)
    export CC=gcc
    export CXX=g++
    export FC=gfortran
    export FFLAGS="-fdefault-real-8 -fdefault-double-8 -fallow-argument-mismatch"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if [[ $(sysctl -n machdep.cpu.brand_string) == *"Apple M1"* ]]; then
            export LAPACK_HOME="/opt/homebrew/include"
            export NETCDF_FORTRAN_HOME="/opt/homebrew/include"
            export CFLAGS="-I/opt/homebrew/include"
            export FFLAGS="$FFLAGS -I/opt/homebrew/include -L/opt/homebrew/lib"
            export LDFLAGS="-L/usr/local/lib"
        else
            export LAPACK_HOME="/usr/local/include"
            export NETCDF_FORTRAN_HOME="/usr/local/include"
            export CFLAGS="-I/usr/local/include"
            export FFLAGS="$FFLAGS -I/usr/local/include -L/usr/local/lib"
            export LDFLAGS="-L/usr/local/lib"
        fi
    else
        export LAPACK_HOME="/usr/include"
        export NETCDF_FORTRAN_HOME="/usr/include"
    fi
    export OMPFLAG="-fopenmp"
fi

git clone -b master git@gitlab.tugraz.at:plasma/codes/gpec.git GPEC
pushd GPEC/install
    make NETCDFLIBS="-lnetcdff -lnetcdf"
popd
