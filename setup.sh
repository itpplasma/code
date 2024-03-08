#!/bin/bash


SCRIPTPATH="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

export CODE=$SCRIPTPATH
#source $CODE/scripts/setup_venv.sh
source $CODE/activate.sh

#cd $CODE/external
#$CODE/scripts/setup_fgsl.sh

cd $CODE
source $CODE/scripts/setup_libneo.sh

