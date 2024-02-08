#!/bin/bash

echo "Setting up GPEC..."
echo "Local use only at ITPcp, not suited for Docker container."
echo "Works with Intel compiler, not GNU or NVIDIA/PGI."
echo "Code access: https://princetonuniversity.github.io/GPEC/developers.html"

module load intel/2018.1
git clone git@github.com:PrincetonUniversity/GPEC.git
cd GPEC/vacuum
make
cd ../gpec
make
