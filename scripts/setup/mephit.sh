#!/bin/bash

set -e
source /etc/profile.d/modules.sh
module use -a $CODE/modules

pushd MEPHIT
../scripts/checkout_branch.sh $CODE_BRANCH
mkdir build
cd build
export LIBNEO_DIR=$CODE/libneo/build
module load mfem
module load fgsl
cmake ..
make -j4
popd
