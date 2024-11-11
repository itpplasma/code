#!/bin/bash

SCRIPTPATH="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

export CODE=$SCRIPTPATH

source $CODE/scripts/setup/venv.sh
source $CODE/activate.sh

pushd $CODE/external
$CODE/scripts/setup/fgsl.sh
popd

pushd $CODE
source $CODE/scripts/setup/libneo.sh
popd

source $CODE/activate.sh
