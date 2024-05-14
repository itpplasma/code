#!/bin/bash

export CODE="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

if [ -n "$CI_MERGE_REQUEST_SOURCE_BRANCH_NAME" ]; then
    export CODE_BRANCH=$CI_MERGE_REQUEST_SOURCE_BRANCH_NAME
else
    pushd $CODE
    export CODE_BRANCH=$(git branch --show-current)
    popd
fi

echo "Activating $CODE on branch $CODE_BRANCH"

source $CODE/scripts/util.sh
add_to_path $CODE/scripts
export PATH
add_to_library_path $CODE/libneo/build
add_to_library_path $CODE/efit_to_boozer/build

if [ -d $CODE/external/scalapack ]; then
    add_to_library_path $CODE/external/scalapack/build/lib
fi
export LD_LIBRARY_PATH

source $CODE/.venv/bin/activate

source /etc/profile.d/modules.sh
module use -a $CODE/modules
