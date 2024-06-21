#!/bin/bash

SCRIPTPATH="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

export CODE=$SCRIPTPATH

if [ -f /etc/debian_version ]; then
    $CODE/scripts/setup_local.sh
fi

source $CODE/scripts/setup_venv.sh
source $CODE/activate.sh

pushd $CODE/external
$CODE/scripts/setup_fgsl.sh
popd

pushd $CODE
source $CODE/scripts/setup_libneo.sh
popd

source $CODE/activate.sh
