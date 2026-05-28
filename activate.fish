#!/usr/bin/env fish

# Fish shell activation script
# Usage: source activate.fish

# INFRA is this repository; CODE is the workspace one level up that holds it
# next to the code checkouts. The codes resolve dependencies as $CODE/<name>.
set SCRIPT_DIR (dirname (status --current-filename))
if type -q path
    set -gx INFRA (path resolve $SCRIPT_DIR)
else if type -q realpath
    set -gx INFRA (realpath $SCRIPT_DIR)
else
    # Fallback: temporarily cd and restore
    set -l __oldpwd $PWD
    cd $SCRIPT_DIR
    set -gx INFRA $PWD
    cd $__oldpwd
end
set -gx CODE (path resolve $INFRA/..)

echo "Setting CODE workspace to: $CODE (infra: $INFRA)"

# Check if the OS is macOS
if test (uname) = "Darwin"
    echo "Running on macOS"

    # Check the processor type
    if test (uname -m) = "arm64"
        echo "This is an Apple Silicon Mac."
        set -gx BLAS_LIBRARIES /opt/homebrew/opt/openblas/lib/libopenblas.dylib
        set -gx LAPACK_LIBRARIES /opt/homebrew/opt/openblas/lib/liblapack.dylib
    else if test (uname -m) = "x86_64"
        echo "This is an Intel Mac."
        set -gx BLAS_LIBRARIES /usr/local/opt/openblas/lib/libopenblas.dylib
        set -gx LAPACK_LIBRARIES /usr/local/opt/openblas/lib/liblapack.dylib
    else
        echo "Unknown processor architecture."
    end

    set -gx CMAKE_INCLUDE_PATH "/opt/homebrew/include/suitesparse:$INFRA/external/triangle"
    set -gx CMAKE_LIBRARY_PATH "/opt/homebrew/lib:$INFRA/external/triangle/build"
    set -gx CMAKE_ARGS "-DBLAS_LIBRARIES=$BLAS_LIBRARIES -DLAPACK_LIBRARIES=$LAPACK_LIBRARIES"
else
    set -gx CMAKE_ARGS ""
end

# Fish-specific utility functions
function add_to_path_fish
    set dir $argv[1]
    if test -d "$dir"
        if not contains "$dir" $PATH
            set -gx PATH $dir $PATH
        end
    end
end

function add_to_library_path_fish
    set dir $argv[1]
    if test -d "$dir"
        if not contains "$dir" $LD_LIBRARY_PATH
            set -gx LD_LIBRARY_PATH $dir $LD_LIBRARY_PATH
        end
    end
    if test (uname) = "Darwin"
        if test -d "$dir"
            if not contains "$dir" $DYLD_LIBRARY_PATH
                set -gx DYLD_LIBRARY_PATH $dir $DYLD_LIBRARY_PATH
            end
        end
    end
end

# Set branch information
function set_branch_fish
    if test -n "$CI_MERGE_REQUEST_SOURCE_BRANCH_NAME"
        set -gx CODE_BRANCH $CI_MERGE_REQUEST_SOURCE_BRANCH_NAME
    else if test -n "$CI_COMMIT_REF_NAME"
        set -gx CODE_BRANCH $CI_COMMIT_REF_NAME
    else
        # Avoid directory changes; query git directly in $INFRA
        set -gx CODE_BRANCH (git -C $INFRA branch --show-current)
        if test -z "$CODE_BRANCH"
            set -gx CODE_BRANCH (git -C $INFRA rev-parse --short HEAD)
        end
    end
    echo "Activating $INFRA on branch $CODE_BRANCH"
end

# Set up paths
set_branch_fish
add_to_path_fish $INFRA/scripts
add_to_path_fish $INFRA/local/bin
add_to_path_fish $INFRA/bin

add_to_library_path_fish $CODE/libneo/build
add_to_library_path_fish $INFRA/local/lib
add_to_library_path_fish $INFRA/lib

# Activate Python virtual environment
if test -f $INFRA/.venv/bin/activate.fish
    source $INFRA/.venv/bin/activate.fish
else
    echo "Warning: Python virtual environment fish activation script not found"
    echo "Run: python -m venv $INFRA/.venv to create it"
end

# Load modules if available
if test -f /etc/profile.d/modules.sh
    # Fish can't directly source shell scripts, but we can try to set up modules
    echo "Note: Module system may require manual setup in fish shell"
end

# Create aliases
alias cdcode="cd $CODE"
if command -v code > /dev/null
    alias vscode="code $CODE"
end

module use -a $INFRA/modules

echo "Fish shell activation complete!"
