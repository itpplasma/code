#!/bin/bash
#
# https://princetonuniversity.github.io/FOCUS/
#

git clone https://github.com/PrincetonUniversity/FOCUS.git

pushd FOCUS
pushd sources
sed -i "s|\$(HDF5_HOME)/include|/usr/include/hdf5/openmpi|g; s|\$(HDF5_HOME)/lib|/usr/lib/$(uname -m)-linux-gnu/hdf5/openmpi|g" Makefile
make CC=gfortran xfocus -j$(nproc)
popd
pushd python

popd
popd
