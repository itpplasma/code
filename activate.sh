#!/bin/bash

SCRIPTPATH="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
export CODE=$SCRIPTPATH

if [ -n "$CI_MERGE_REQUEST_SOURCE_BRANCH_NAME" ]; then
    export CODE_BRANCH=$CI_MERGE_REQUEST_SOURCE_BRANCH_NAME
else
    cd $CODE
    export CODE_BRANCH=$(git branch --show-current)
    cd -
fi

echo "Activating $CODE on branch $CODE_BRANCH"

source $CODE/scripts/util.sh
add_to_path $CODE/scripts
export PATH
add_to_library_path $CODE/libneo/build
add_to_library_path $CODE/efit_to_boozer/build
export LD_LIBRARY_PATH

if [ -n "$VIRTUAL_ENV" ]; then
    deactivate > /dev/null 2>&1;
fi

source $CODE/.venv/bin/activate

source /etc/profile.d/modules.sh
module use -a $CODE/modules
