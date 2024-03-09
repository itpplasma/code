#!/bin/bash

SCRIPTPATH="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
export CODE="$(realpath "$SCRIPTPATH/..")"

source /etc/profile.d/modules.sh
module use -a $CODE/modules

cd MEPHIT
../scripts/checkout_branch.sh $CI_MERGE_REQUEST_SOURCE_BRANCH_NAME
mkdir build
cd build
export LIBNEO_DIR=$CODE/libneo/build
module load mfem
module load fgsl
cmake ..
make -j4
