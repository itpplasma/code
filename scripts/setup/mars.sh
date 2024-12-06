#!/bin/bash

set -e
module load nvhpc/24.5
git clone git@gitlab.tugraz.at:plasma/codes/mars.git MARS
cd MARS/MarsQ_2022
make
