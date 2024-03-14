#!/bin/bash

if [ ! -d "fgsl-1.5.0" ] ; then
    echo "Fetching and building FGSL..."
    curl -L https://doku.lrz.de/files/10746505/10746508/1/1684600947173/fgsl-1.5.0.tar.gz -o - | tar xzv
    cd fgsl-1.5.0
    ./configure
    make
    cd ..
fi
