#!/usr/bin/env bash

cd $INFRA/external

if [ ! -d "simsopt" ] ; then
    echo "Fetching and building simsopt..."
    git clone git@github.com:itpplasma/simsopt.git
fi

pushd simsopt
pip install --no-build-isolation -e .
popd
