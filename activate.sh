#!/bin/bash

SCRIPTPATH="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
export CODE=$SCRIPTPATH
source $CODE/scripts/util.sh
add_to_path $CODE/scripts
export PATH
add_to_library_path $CODE/libneo/build
add_to_library_path $CODE/efit_to_boozer/build
export LD_LIBRARY_PATH

if [ -n "$VIRTUAL_ENV" ]; then
    deactivate > /dev/null 2>&1;
fi

echo "Activating $CODE/.venv"
source $CODE/.venv/bin/activate

source /etc/profile.d/modules.sh
module use -a $CODE/modules
