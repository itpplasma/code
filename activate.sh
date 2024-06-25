#!/bin/bash

export CODE="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

source $CODE/scripts/util.sh
set_branch
add_to_path $CODE/scripts
export PATH
add_to_library_path $CODE/libneo/build
add_to_library_path $CODE/efit_to_boozer/build
export LD_LIBRARY_PATH

source $CODE/.venv/bin/activate

source /etc/profile.d/modules.sh
module use -a $CODE/modules
