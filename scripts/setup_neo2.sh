#!/bin/bash
pushd NEO-2
$CODE/scripts/checkout_branch.sh $CODE_BRANCH

# Build NEO-2-PAR
pushd NEO-2-PAR/Build-Debug
cmake .. -DNEO2_Libs=$CODE/libneo/build -DFGSL_INC=$CODE/external/fgsl-1.6.0 -DFGSL_LIB=$CODE/external/fgsl-1.6.0/.libs
make
popd

# Build NEO-2-QL
pushd NEO-2-QL
mkdir Build
cd Build
cmake .. -DNEO2_Libs=$CODE/libneo/build -DFGSL_INC=$CODE/external/fgsl-1.6.0 -DFGSL_LIB=$CODE/external/fgsl-1.6.0/.libs
make
popd

# Installing Python packages for NEO-2 (requires libneo)
pushd python
pip install -e .
popd

popd
