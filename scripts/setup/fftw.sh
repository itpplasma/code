#!/usr/bin/env bash

cd "$CODE/external" || exit 1

FFTW_VERSION=3.3.10
INSTALL_PREFIX="$CODE/external/fftw-${FFTW_VERSION}/install"

# Check if system FFTW is available
if [ -f /usr/include/fftw3.h ] || [ -f /usr/local/include/fftw3.h ]; then
    echo "System FFTW headers found"
    exit 0
fi

# Check if already built
if [ -f "$INSTALL_PREFIX/lib/libfftw3.a" ]; then
    echo "FFTW already built in $INSTALL_PREFIX"
    exit 0
fi

echo "Fetching and building FFTW ${FFTW_VERSION}..."
if [ ! -d "fftw-${FFTW_VERSION}" ]; then
    curl -L https://www.fftw.org/fftw-${FFTW_VERSION}.tar.gz -o - | tar xz
fi

cd fftw-${FFTW_VERSION}
./configure --prefix="$INSTALL_PREFIX" \
            --enable-shared \
            --enable-threads \
            --enable-openmp
make -j$(nproc)
make install

echo "FFTW installed to $INSTALL_PREFIX"
