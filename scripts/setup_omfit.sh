#!/bin/bash

echo "Setting up OMFIT..."
echo "Requires personal Git access to OMFIT repository."
echo "Code access: https://omfit.io/install.html"

deactivate

pushd $CODE/external

    git clone --filter=blob:none --recursive -b unstable git@github.com:gafusion/OMFIT-source.git
    OMFIT-source/install/install.sh

    pushd OMFIT-source
        export OMAS_ROOT=$PWD/omas
        export USER=`whoami`
        python -m pip install -r install/requirements.txt
        python -m pip install -e .
        mkdir -p $HOME/.LICENSES
        cp $CODE/scripts/OMFIT/LICENSES/* $HOME/.LICENSES/
    popd

popd
