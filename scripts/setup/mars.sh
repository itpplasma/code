#!/usr/bin/env bash

set -e
module load intel/compiler-rt/2024.2.1 intel/ifort intel/mpi
cd $CODE/external/intel
git clone git@gitlab.tugraz.at:plasma/codes/mars.git MARS
cd MARS/MarsQ_2022
make
cd ../CheaseMerge
make
