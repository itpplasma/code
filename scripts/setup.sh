#!/usr/bin/env bash

set -e

SCRIPTPATH="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

export CODE=$SCRIPTPATH/..
export GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new"

$CODE/scripts/setup/venv.sh
source $CODE/.venv/bin/activate

pushd $CODE/external
    $CODE/scripts/setup/openblas.sh
    $CODE/scripts/setup/gsl.sh
    $CODE/scripts/setup/fgsl.sh
    $CODE/scripts/setup/fftw.sh
    $CODE/scripts/setup/netcdf.sh
    $CODE/scripts/setup/triangle.sh
popd

pushd $CODE
    $CODE/scripts/setup/libneo.sh
popd
