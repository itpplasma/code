#!/usr/bin/env bash

cd "$CODE/external" || exit 1

if [ ! -f "fgsl-1.6.0/.libs/libfgsl.a" ] ; then
    echo "Fetching and building FGSL..."

    # Determine GSL location
    if command -v gsl-config &> /dev/null; then
        GSL_CONFIG=gsl-config
    else
        GSL_PREFIX=$CODE/external/gsl-2.8/install
        if [ -d "$GSL_PREFIX" ]; then
            GSL_CONFIG="$GSL_PREFIX/bin/gsl-config"
            export LD_LIBRARY_PATH="$GSL_PREFIX/lib:$LD_LIBRARY_PATH"
        fi
    fi

    if [ ! -x "$GSL_CONFIG" ] && ! command -v "$GSL_CONFIG" &> /dev/null; then
        echo "ERROR: GSL not found. Run gsl.sh first."
        exit 1
    fi

    # Set variables to bypass pkg-config
    export gsl_CFLAGS="$($GSL_CONFIG --cflags)"
    export gsl_LIBS="$($GSL_CONFIG --libs)"

    if [ ! -d "fgsl-1.6.0" ]; then
        curl -L https://github.com/reinh-bader/fgsl/archive/refs/tags/v1.6.0.tar.gz -o - | tar xz
    fi
    pushd fgsl-1.6.0
    mkdir -p m4
    # Download pkg.m4 if pkg-config not installed (needed for autoreconf)
    if ! command -v pkg-config &> /dev/null && [ ! -f m4/pkg.m4 ]; then
        curl -L https://raw.githubusercontent.com/pkgconf/pkgconf/master/pkg.m4 -o m4/pkg.m4
    fi
    autoreconf -i
    ./configure
    make -j$(nproc)
    popd
fi
