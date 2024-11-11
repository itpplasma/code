#!/bin/bash

set -e
if [ ! -d "CHEASE" ] ; then
    echo "Fetching and building CHEASE..."
    git clone https://gitlab.epfl.ch/spc/chease.git CHEASE
    cd CHEASE/src-f90/
    make
fi
