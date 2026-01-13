#!/usr/bin/env bash
# NVIDIA HPC SDK setup script (optional - not included in default setup.sh)
# Installs to $CODE/external/nvhpc without requiring root access

set -e

NVHPC_VERSION=25.11
NVHPC_YEAR=2025
CUDA_VERSION=13.0

INSTALL_DIR="$CODE/external/nvhpc"

cd "$CODE/external" || exit 1

# Check if already installed
if [ -f "$INSTALL_DIR/Linux_x86_64/$NVHPC_VERSION/compilers/bin/nvfortran" ]; then
    echo "NVIDIA HPC SDK $NVHPC_VERSION already installed in $INSTALL_DIR"
    exit 0
fi

echo "Installing NVIDIA HPC SDK $NVHPC_VERSION to $INSTALL_DIR..."

TARBALL="nvhpc_${NVHPC_YEAR}_${NVHPC_VERSION//./}_Linux_x86_64_cuda_${CUDA_VERSION}.tar.gz"
URL="https://developer.download.nvidia.com/hpc-sdk/$NVHPC_VERSION/$TARBALL"

# Download if not present
if [ ! -f "$TARBALL" ]; then
    echo "Downloading $TARBALL..."
    curl -L "$URL" -o "$TARBALL"
fi

# Extract
EXTRACT_DIR="nvhpc_${NVHPC_YEAR}_${NVHPC_VERSION//./}_Linux_x86_64_cuda_${CUDA_VERSION}"
if [ ! -d "$EXTRACT_DIR" ]; then
    echo "Extracting..."
    tar xzf "$TARBALL"
fi

# Install silently to our custom location
mkdir -p "$INSTALL_DIR"
cd "$EXTRACT_DIR"

export NVHPC_SILENT=true
export NVHPC_INSTALL_DIR="$INSTALL_DIR"
export NVHPC_INSTALL_TYPE=single
export NVHPC_INSTALL_LOCAL_DIR=""

./install

cd "$CODE/external"

# Verify installation
if [ -f "$INSTALL_DIR/Linux_x86_64/$NVHPC_VERSION/compilers/bin/nvfortran" ]; then
    echo ""
    echo "NVIDIA HPC SDK $NVHPC_VERSION installed successfully!"
    echo "Location: $INSTALL_DIR"
    echo ""
    echo "To use, source activate.sh - it will detect and configure paths automatically."
else
    echo "ERROR: Installation failed"
    exit 1
fi
