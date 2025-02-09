#!/usr/bin/env bash

set -e
module load nvhpc/24.1
git clone git@gitlab.tugraz.at:plasma/codes/mars.git MARS
cd MARS/MarsQ_2022
make
cd ../CheaseMerge
make
