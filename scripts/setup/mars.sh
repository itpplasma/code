#!/usr/bin/env bash

set -e
module load intel/ifort intel/mkl intel/mpi
cd $CODE/external/intel
git clone git@gitlab.tugraz.at:plasma/codes/mars.git MARS
cd MARS/MarsQ_2022
make
cd ../CheaseMerge
make
