#!/bin/bash
CI_PROJECT_DIR=$CODE
cd NEO-2
$CODE/scripts/checkout_branch.sh $CODE_BRANCH

# Build NEO-2-PAR
cd NEO-2-PAR/Build-Debug
cmake .. -DNEO2_Libs=$CODE/libneo/build -DFGSL_INC=$CODE/external/fgsl-1.5.0 -DFGSL_LIB=$CODE/external/fgsl-1.5.0/.libs
make

# Build NEO-2-QL
cd ../../NEO-2-QL
mkdir Build
cd Build
cmake .. -DNEO2_Libs=$CODE/libneo/build -DFGSL_INC=$CODE/external/fgsl-1.5.0 -DFGSL_LIB=$CODE/external/fgsl-1.5.0/.libs
make

# Installing Python packages for NEO-2 (requires libneo)
cd ../../..
cd NEO-2/python
pip install -e .
