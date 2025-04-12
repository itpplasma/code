#!/usr/bin/env bash

pushd $CODE/external

if [ ! -d "STELLOPT" ] ; then
    echo "Fetching and building STELLOPT..."
    git clone --filter=blob:none https://github.com/PrincetonUniversity/STELLOPT.git STELLOPT
fi
    pushd STELLOPT
        export STELLOPT_PATH=$PWD
        export MYHOME=STELLOPT_PATH/build
        export MACHINE=ubuntu
        bash build_all
        find . -name "*.o" | xargs rm
    popd
popd
