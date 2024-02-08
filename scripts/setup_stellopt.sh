#!/bin/bash

if [ ! -d "STELLOPT" ] ; then
    echo "Fetching and building STELLOPT..."
    git clone https://github.com/PrincetonUniversity/STELLOPT.git STELLOPT
    cd STELLOPT
    export STELLOPT_PATH=$PWD
    export MACHINE=ubuntu
    ./build_all
fi
