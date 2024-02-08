#!/bin/bash

module load /temp/AG-plasma/opt/nvidia/hpc_sdk/modulefiles/nvhpc/24.1
git clone git@gitlab.tugraz.at:plasma/codes/mars.git MARS
cd MARS/MarsQ_2022
make
