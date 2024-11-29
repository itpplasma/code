#!/bin/bash

export CODE="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Check if the OS is macOS
if [[ "$(uname)" == "Darwin" ]]; then
    echo "Running on macOS"
    export CC=gcc-14
    export CXX=g++-14
    export FC=gfortran-14

    # Check the processor type
    if [[ "$(uname -m)" == "arm64" ]]; then
        echo "This is an Apple Silicon (M1/M2) Mac."
        export BLAS_LIBRARIES=/opt/homebrew/opt/openblas/lib/libopenblas.dylib
        export LAPACK_LIBRARIES=/opt/homebrew/opt/openblas/lib/liblapack.dylib
    elif [[ "$(uname -m)" == "x86_64" ]]; then
        echo "This is an Intel Mac."
        export BLAS_LIBRARIES=/usr/local/opt/openblas/lib/libopenblas.dylib
        export LAPACK_LIBRARIES=/usr/local/opt/openblas/lib/liblapack.dylib
    else
        echo "Unknown processor architecture."
    fi

    export CMAKE_ARGS="-DBLAS_LIBRARIES=$BLAS_LIBRARIES \
        -DLAPACK_LIBRARIES=$LAPACK_LIBRARIES \
        -DCMAKE_INCLUDE_PATH=/opt/homebrew/include/suitesparse"

else
    echo "Not running on macOS."
    export CMAKE_ARGS=""
fi

source $CODE/scripts/util.sh
set_branch
add_to_path $CODE/scripts
add_to_path $CODE/local/bin
add_to_path $CODE/bin
export PATH
add_to_library_path $CODE/libneo/build
add_to_library_path $CODE/efit_to_boozer/build
add_to_library_path $CODE/local/lib
add_to_library_path $CODE/lib
export LD_LIBRARY_PATH

source $CODE/.venv/bin/activate

source /etc/profile.d/modules.sh
module use -a $CODE/modules
