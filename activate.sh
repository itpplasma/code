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

export CODE="$( cd "$( dirname "$SCRIPT_SOURCE" )" && pwd )"

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
fi

if [ -n "$FISH_VERSION" ]; then
    # Fish shell detected - redirect to dedicated fish script
    echo "Fish shell detected!"
    echo "Please use the dedicated fish script instead:"
    echo "  source $CODE/activate.fish"
    echo ""
    echo "This script (activate.sh) is designed for bash/zsh compatibility."
    return 1
    
else
    # Bash/Zsh setup (original behavior)
    source $CODE/scripts/util.sh
    set_branch
    add_to_path $CODE/scripts
    add_to_path $CODE/local/bin
    add_to_path $CODE/bin
    export PATH
    add_to_library_path $CODE/libneo/build
    add_to_library_path $CODE/local/lib
    add_to_library_path $CODE/lib
    # Add module library paths (LD_RUN_PATH is set by spack modules)
    if [ -n "$LD_RUN_PATH" ]; then
        export LD_LIBRARY_PATH="$LD_RUN_PATH:$LD_LIBRARY_PATH"
    fi
    export LD_LIBRARY_PATH

    source $CODE/.venv/bin/activate

    if [ -f /etc/profile.d/modules.sh ]; then
        unalias ml 2>/dev/null
        source /etc/profile.d/modules.sh
    fi

    if command -v module >/dev/null 2>&1; then
        module use -a $CODE/modules
    fi
fi
