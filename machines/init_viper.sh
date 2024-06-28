#!/bin/bash

module load cmake/3.28
module load gcc/14
module load openmpi/4.1
module load mkl_parts/1
module load hdf5-serial/1.14.1
module load netcdf-serial/4.9.2
module load gsl/2.7

export FC=$(which gfortran)
export CC=$(which gcc)
export CXX=$(which g++)

export LD_LIBRARY_PATH=$HOME/.local/lib:$LD_LIBRARY_PATH
