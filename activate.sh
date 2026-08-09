#!/usr/bin/env bash

# Determine the script location for bash/zsh
if [ -n "$BASH_SOURCE" ]; then
    # Running in bash
    SCRIPT_SOURCE="${BASH_SOURCE[0]}"
elif [ -n "$ZSH_VERSION" ]; then
    # Running in zsh
    SCRIPT_SOURCE="${(%):-%x}"
else
    # Fallback
    SCRIPT_SOURCE="$0"
fi

# INFRA is this repository. CODE is the workspace one level up that holds it
# next to the actual code checkouts (libneo, SIMPLE, ...). The codes resolve
# their dependencies as $CODE/<name> (find_or_fetch), so CODE is the parent.
export INFRA="$( cd "$( dirname "$SCRIPT_SOURCE" )" && pwd )"
export CODE="$( cd "$INFRA/.." && pwd )"

# Check if the OS is macOS
if [ "$(uname)" = "Darwin" ]; then
    echo "Running on macOS"

    # Check the processor type
    if [ "$(uname -m)" = "arm64" ]; then
        echo "This is an Apple Silicon Mac."
        export BLAS_LIBRARIES=/opt/homebrew/opt/openblas/lib/libopenblas.dylib
        export LAPACK_LIBRARIES=/opt/homebrew/opt/openblas/lib/liblapack.dylib
    elif [ "$(uname -m)" = "x86_64" ]; then
        echo "This is an Intel Mac."
        export BLAS_LIBRARIES=/usr/local/opt/openblas/lib/libopenblas.dylib
        export LAPACK_LIBRARIES=/usr/local/opt/openblas/lib/liblapack.dylib
    else
        echo "Unknown processor architecture."
    fi

    export CMAKE_INCLUDE_PATH="/opt/homebrew/include/suitesparse:$CODE/external/triangle"
    export CMAKE_LIBRARY_PATH="/opt/homebrew/lib:$CODE/external/triangle/build"
    export CMAKE_ARGS="-DBLAS_LIBRARIES=$BLAS_LIBRARIES -DLAPACK_LIBRARIES=$LAPACK_LIBRARIES"
else
    export CMAKE_ARGS=""

    # Use locally built OpenBLAS if system BLAS headers not available
    if [ ! -f /usr/include/cblas.h ] && [ ! -f /usr/include/openblas/cblas.h ]; then
        OPENBLAS_PREFIX="$CODE/external/OpenBLAS-0.3.28/install"
        if [ -d "$OPENBLAS_PREFIX" ]; then
            export BLAS_LIBRARIES="$OPENBLAS_PREFIX/lib/libopenblas.so"
            export LAPACK_LIBRARIES="$OPENBLAS_PREFIX/lib/libopenblas.so"
            export OpenBLAS_ROOT="$OPENBLAS_PREFIX"
            export CMAKE_ARGS="-DBLAS_LIBRARIES=$BLAS_LIBRARIES -DLAPACK_LIBRARIES=$LAPACK_LIBRARIES"
        fi
    fi

    # Use locally built GSL only if system GSL not available
    if ! command -v gsl-config &> /dev/null && [ -d "$CODE/external/gsl-2.8/install" ]; then
        export GSL_ROOT_DIR="$CODE/external/gsl-2.8/install"
        export PKG_CONFIG_PATH="$GSL_ROOT_DIR/lib/pkgconfig:$PKG_CONFIG_PATH"
    fi

    # Use locally built FFTW if system headers not available
    if [ ! -f /usr/include/fftw3.h ] && [ -d "$CODE/external/fftw-3.3.10/install" ]; then
        export FFTW_ROOT="$CODE/external/fftw-3.3.10/install"
    fi

    # Use locally built HDF5/NetCDF if system nf-config not available
    if ! command -v nf-config &> /dev/null && [ -d "$CODE/external/netcdf-install" ]; then
        export HDF5_ROOT="$CODE/external/netcdf-install"
        export NETCDF_ROOT="$CODE/external/netcdf-install"
        export PATH="$CODE/external/netcdf-install/bin:$PATH"
    fi

    ENZYME_PREFIX="$CODE/enzyme/current"
    if [ -d "$ENZYME_PREFIX/lib" ]; then
        export ENZYME_HOME="$ENZYME_PREFIX"
        export ENZYME_ROOT="$ENZYME_PREFIX"
        export ENZYME_PLUGIN_DIR="$ENZYME_PREFIX/lib"
        export ENZYME_CMAKE_DIR="$ENZYME_PREFIX/lib/cmake/Enzyme"
        export Enzyme_ROOT="$ENZYME_PREFIX"
        export Enzyme_DIR="$ENZYME_CMAKE_DIR"
        if [ -f "$ENZYME_PLUGIN_DIR/LLVMEnzyme-22.so" ]; then
            export ENZYME_PLUGIN="$ENZYME_PLUGIN_DIR/LLVMEnzyme-22.so"
        elif [ -f "$ENZYME_PLUGIN_DIR/LLVMEnzyme.so" ]; then
            export ENZYME_PLUGIN="$ENZYME_PLUGIN_DIR/LLVMEnzyme.so"
        fi
        case ":${CMAKE_PREFIX_PATH:-}:" in
            *":$ENZYME_PREFIX:"*) ;;
            *) export CMAKE_PREFIX_PATH="$ENZYME_PREFIX${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}" ;;
        esac
    fi
    unset ENZYME_PREFIX

    # Use locally installed NVIDIA HPC SDK if available (optional)
    NVHPC_ROOT="$CODE/external/nvhpc/Linux_x86_64"
    if [ -d "$NVHPC_ROOT" ]; then
        # Find the installed version
        NVHPC_VERSION=$(ls "$NVHPC_ROOT" 2>/dev/null | grep -E '^[0-9]+\.[0-9]+$' | sort -V | tail -1)
        if [ -n "$NVHPC_VERSION" ]; then
            export NVHPC="$CODE/external/nvhpc"
            export NVHPC_ROOT="$NVHPC_ROOT/$NVHPC_VERSION"
            export PATH="$NVHPC_ROOT/compilers/bin:$PATH"
            export PATH="$NVHPC_ROOT/comm_libs/mpi/bin:$PATH"
            export MANPATH="$NVHPC_ROOT/compilers/man:$MANPATH"
            export NVHPC_CUDA_HOME="$NVHPC_ROOT/cuda"
        fi
    fi
fi

if [ -n "$FISH_VERSION" ]; then
    # Fish shell detected - redirect to dedicated fish script
    echo "Fish shell detected!"
    echo "Please use the dedicated fish script instead:"
    echo "  source $INFRA/activate.fish"
    echo ""
    echo "This script (activate.sh) is designed for bash/zsh compatibility."
    return 1
    
else
    # Bash/Zsh setup (original behavior)
    source $INFRA/scripts/util.sh
    set_branch
    add_to_path $INFRA/scripts
    add_to_path $INFRA/local/bin
    add_to_path $INFRA/bin
    export PATH
    add_to_library_path $CODE/libneo/build
    add_to_library_path $INFRA/local/lib
    add_to_library_path $INFRA/lib
    if [ -n "$GSL_ROOT_DIR" ]; then
        add_to_library_path $GSL_ROOT_DIR/lib
        add_to_path $GSL_ROOT_DIR/bin
    fi
    if [ -n "$OpenBLAS_ROOT" ]; then
        add_to_library_path $OpenBLAS_ROOT/lib
    fi
    if [ -n "$FFTW_ROOT" ]; then
        add_to_library_path $FFTW_ROOT/lib
    fi
    if [ -n "$HDF5_ROOT" ]; then
        add_to_library_path $HDF5_ROOT/lib
    fi
    if [ -n "${ENZYME_PLUGIN_DIR:-}" ]; then
        add_to_library_path $ENZYME_PLUGIN_DIR
    fi
    if [ -n "$NVHPC_ROOT" ] && [ -d "$NVHPC_ROOT/compilers/lib" ]; then
        add_to_library_path $NVHPC_ROOT/compilers/lib
        add_to_library_path $NVHPC_ROOT/cuda/lib64
        add_to_library_path $NVHPC_ROOT/math_libs/lib64
    fi
    # Add module library paths (LD_RUN_PATH is set by spack modules)
    if [ -n "$LD_RUN_PATH" ]; then
        export LD_LIBRARY_PATH="$LD_RUN_PATH:$LD_LIBRARY_PATH"
    fi
    export LD_LIBRARY_PATH

    source $INFRA/.venv/bin/activate

    if [ -f /etc/profile.d/modules.sh ]; then
        unalias ml 2>/dev/null
        source /etc/profile.d/modules.sh
    fi

    if command -v module >/dev/null 2>&1; then
        module use -a $INFRA/modules
    fi
fi
