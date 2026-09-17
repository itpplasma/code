#!/usr/bin/env bash

set -e

SCRIPTPATH="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

export INFRA="$( cd "$SCRIPTPATH/.." && pwd )"
export CODE="$( cd "$INFRA/.." && pwd )"
export GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new"

# venv and requirements.txt live at the infra root
pushd $INFRA
    $INFRA/scripts/setup/venv.sh
    source $INFRA/.venv/bin/activate
popd

# external prebuilt deps are workspace-shared; the codes read $CODE/external
mkdir -p $CODE/external
pushd $CODE/external
    $INFRA/scripts/setup/openblas.sh
    $INFRA/scripts/setup/gsl.sh
    $INFRA/scripts/setup/fgsl.sh
    $INFRA/scripts/setup/fftw.sh
    $INFRA/scripts/setup/netcdf.sh
    $INFRA/scripts/setup/triangle.sh
popd

pushd $CODE
    $INFRA/scripts/setup/libneo.sh
popd
