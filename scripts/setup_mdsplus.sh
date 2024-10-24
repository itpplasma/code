#!/bin/bash

sudo apt-get install autoconf-archive libtool libreadline-dev default-jdk libmotif-dev

pushd $CODE/external
    git clone git@github.com:MDSplus/mdsplus.git
    autoupdate
    autoreconf -i -I m4
    mkdir build
    pushd build
        ../configure --disable-dependency-tracking
    popd build
popd
