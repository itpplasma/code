#!/usr/bin/env bash

OPENBLAS_VERSION=0.3.28

cd "$CODE/external" || exit 1

# Check if system BLAS/LAPACK dev headers exist
if [ -f /usr/include/cblas.h ] || [ -f /usr/include/openblas/cblas.h ]; then
    echo "System BLAS headers found"
elif [ ! -f "OpenBLAS-${OPENBLAS_VERSION}/install/lib/libopenblas.a" ]; then
    echo "Fetching and building OpenBLAS ${OPENBLAS_VERSION}..."
    if [ ! -d "OpenBLAS-${OPENBLAS_VERSION}" ]; then
        curl -L https://github.com/OpenMathLib/OpenBLAS/releases/download/v${OPENBLAS_VERSION}/OpenBLAS-${OPENBLAS_VERSION}.tar.gz -o - | tar xz
    fi
    cd OpenBLAS-${OPENBLAS_VERSION}
    # Build with ZEN target for AMD EPYC
    make -j$(nproc) TARGET=ZEN USE_OPENMP=1
    make PREFIX=$CODE/external/OpenBLAS-${OPENBLAS_VERSION}/install install
fi
