#!/bin/bash

SCRIPTPATH="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
export CODE=$SCRIPTPATH
source $CODE/scripts/util.sh
add_to_path $CODE/scripts
export PATH

if [ -n "$VIRTUAL_ENV" ]; then
    deactivate > /dev/null 2>&1;
fi

echo "Activating $CODE/.venv"
source $CODE/.venv/bin/activate

module use -a $CODE/modules
