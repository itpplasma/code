#!/usr/bin/env bash

GSL_VERSION=2.8

cd "$CODE/external" || exit 1

if command -v gsl-config &> /dev/null; then
    echo "System GSL found: $(gsl-config --version)"
elif [ ! -f "gsl-${GSL_VERSION}/install/lib/libgsl.a" ]; then
    echo "Fetching and building GSL ${GSL_VERSION}..."
    if [ ! -d "gsl-${GSL_VERSION}" ]; then
        curl -L https://ftp.gnu.org/gnu/gsl/gsl-${GSL_VERSION}.tar.gz -o - | tar xz
    fi
    pushd gsl-${GSL_VERSION}
    ./configure --prefix=$CODE/external/gsl-${GSL_VERSION}/install
    make -j$(nproc)
    make install
    popd
fi
