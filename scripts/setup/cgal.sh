#!/usr/bin/env bash
# CGAL setup script - downloads and installs CGAL header-only library
# CGAL 5.x is header-only and only requires Boost headers

set -e

cd "$CODE/external" || exit 1

CGAL_VERSION="5.6.1"
CGAL_DIR="CGAL-${CGAL_VERSION}"
CGAL_INSTALL="$CODE/external/cgal"

if [ ! -d "$CGAL_INSTALL/include/CGAL" ]; then
    echo "Fetching and installing CGAL ${CGAL_VERSION}..."

    # Download CGAL
    if [ ! -d "$CGAL_DIR" ]; then
        echo "Downloading CGAL ${CGAL_VERSION}..."
        curl -L "https://github.com/CGAL/cgal/releases/download/v${CGAL_VERSION}/CGAL-${CGAL_VERSION}.tar.xz" -o "CGAL-${CGAL_VERSION}.tar.xz"
        tar xf "CGAL-${CGAL_VERSION}.tar.xz"
        rm "CGAL-${CGAL_VERSION}.tar.xz"
    fi

    # Create install directory structure
    mkdir -p "$CGAL_INSTALL"

    # CGAL 5.x is header-only, just copy headers and cmake files
    echo "Installing CGAL headers..."
    cp -r "$CGAL_DIR/include" "$CGAL_INSTALL/"

    # Copy CMake configuration files
    mkdir -p "$CGAL_INSTALL/lib/cmake/CGAL"
    cp -r "$CGAL_DIR/lib/cmake/CGAL/"* "$CGAL_INSTALL/lib/cmake/CGAL/"

    # Clean up source directory
    rm -rf "$CGAL_DIR"

    echo "CGAL ${CGAL_VERSION} installed to $CGAL_INSTALL"
else
    echo "CGAL already installed at $CGAL_INSTALL"
fi

# Print usage instructions
echo ""
echo "To use CGAL with CMake, add to your cmake command:"
echo "  -DCGAL_DIR=$CGAL_INSTALL/lib/cmake/CGAL"
echo ""
echo "Or set environment variable:"
echo "  export CGAL_DIR=$CGAL_INSTALL/lib/cmake/CGAL"
