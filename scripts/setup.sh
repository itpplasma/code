#!/bin/bash

SCRIPTPATH="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

export CODE=$SCRIPTPATH
export GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" 

source $CODE/scripts/setup/venv.sh
source $CODE/activate.sh

pushd $CODE/external
$CODE/scripts/setup/fgsl.sh
popd

pushd $CODE
source $CODE/scripts/setup/libneo.sh
popd

source $CODE/activate.sh
