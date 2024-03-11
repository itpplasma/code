#!/bin/bash
CI_PROJECT_DIR=$CODE
cd NEO-2
../scripts/checkout_branch.sh $CI_COMMIT_BRANCH
# Build NEO-2-PAR
cd NEO-2-PAR/Build-Debug
cmake .. -DNEO2_Libs=$CI_PROJECT_DIR/libneo/build -DFGSL_INC=$CI_PROJECT_DIR/external/fgsl-1.5.0 -DFGSL_LIB=$CI_PROJECT_DIR/external/fgsl-1.5.0/.libs
make
# Build NEO-2-QL
cd ../../NEO-2-QL
mkdir Build
cd Build
cmake .. -DNEO2_Libs=$CI_PROJECT_DIR/libneo/build -DFGSL_INC=$CI_PROJECT_DIR/external/fgsl-1.5.0 -DFGSL_LIB=$CI_PROJECT_DIR/external/fgsl-1.5.0/.libs
make

# TODO Georg
# cd python
# pip install -e .
