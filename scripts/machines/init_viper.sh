#!/usr/bin/env bash

module load gcc/14
module load openmpi/4.1
module load mkl_parts/1
module load hdf5-serial/1.14.1
module load netcdf-serial/4.9.2
module load gsl/2.7
module load fftw-serial/3.3.10
module load cmake/3.28
module load anaconda/3/2023.03

export FC=$(which gfortran)
export CC=$(which gcc)
export CXX=$(which g++)

export C_INCLUDE_PATH=$FFTW_HOME/include
export LD_LIBRARY_PATH=$FFTW_HOME/lib:$LD_LIBRARY_PATH
export LD_LIBRARY_PATH=$HOME/.local/lib:$LD_LIBRARY_PATH
