#!/bin/bash

echo "Setting up GPEC..."
echo "Local use only at ITPcp, not suited for Docker container."
echo "Works with Intel compiler, not GNU or NVIDIA/PGI."
echo "Code access: https://princetonuniversity.github.io/GPEC/developers.html"

module load intelcompiler/2018.1

source setup_netcdf.sh

module load netcdf-fortran/4.6.1-intel

git clone git@github.com:PrincetonUniversity/GPEC.git
cd GPEC/install
make
