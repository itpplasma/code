#!/usr/bin/env fish

# Fish shell activation script
# Usage: source activate.fish

# Get the directory containing this script
set SCRIPT_DIR (dirname (status --current-filename))
set -gx CODE (cd $SCRIPT_DIR; and pwd)

echo "Setting CODE environment to: $CODE"

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

    set -gx CMAKE_INCLUDE_PATH "/opt/homebrew/include/suitesparse:$CODE/external/triangle"
    set -gx CMAKE_LIBRARY_PATH "/opt/homebrew/lib:$CODE/external/triangle/build"
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
        pushd $CODE
        set -gx CODE_BRANCH (git branch --show-current)
        popd
    end
    echo "Activating $CODE on branch $CODE_BRANCH"
end

# Set up paths
set_branch_fish
add_to_path_fish $CODE/scripts
add_to_path_fish $CODE/local/bin
add_to_path_fish $CODE/bin

add_to_library_path_fish $CODE/libneo/build
add_to_library_path_fish $CODE/local/lib
add_to_library_path_fish $CODE/lib

# Activate Python virtual environment
if test -f $CODE/.venv/bin/activate.fish
    source $CODE/.venv/bin/activate.fish
else
    echo "Warning: Python virtual environment fish activation script not found"
    echo "Run: python -m venv $CODE/.venv to create it"
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

echo "Fish shell activation complete!"