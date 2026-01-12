#!/usr/bin/env bash

set -e

source $CODE/scripts/util.sh

echo "Setting up ASCOT5..."

mkdir -p external
pushd external

if [ ! -d "ascot5" ]; then
    echo "Cloning ascot5 from itpplasma fork..."
    clone_github ascot5
    cd ascot5
    echo "Adding upstream remote..."
    git remote add upstream git@github.com:ascot4fusion/ascot5.git
    git fetch upstream
    cd ..
fi

pushd ascot5

echo "Remotes configured:"
git remote -v

# Find h5cc from loaded HDF5 module (handles stale PATH entries)
if [ -n "$CMAKE_PREFIX_PATH" ]; then
    for prefix in $(echo "$CMAKE_PREFIX_PATH" | tr ':' '\n' | grep hdf5); do
        if [ -x "$prefix/bin/h5cc" ]; then
            export HDF5_DIR="$prefix"
            export PATH="$prefix/bin:$PATH"
            echo "Using HDF5 from: $HDF5_DIR"
            break
        fi
    done
fi

# Patch Makefile: spack-built h5cc doesn't support -shlib flag
# (it passes all args directly to the compiler instead of interpreting them)
if grep -q 'CFLAGS += -shlib' src/Makefile; then
    echo "Patching Makefile to remove unsupported -shlib flag..."
    sed -i 's/CFLAGS += -shlib/#CFLAGS += -shlib  # disabled: spack h5cc passes this to gcc/' src/Makefile
fi

# Add HDF5 high-level library (spack h5cc only links hdf5, not hdf5_hl)
# FLAGS is appended to CFLAGS in the Makefile
export FLAGS="-lhdf5_hl"

echo "Building ascot5_main with release flags..."
make -j$(nproc) ascot5_main

echo "Building libascot..."
make -j$(nproc) libascot

echo ""
echo "Build complete. Binaries in: $(pwd)/build/"
ls -la build/

popd
popd
