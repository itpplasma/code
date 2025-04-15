#!/bin/sh

cd $CODE/external
git clone https://github.com/DrTimothyAldenDavis/SuiteSparse
cd SuiteSparse/build
git checkout v7.10.2
cmake -GNinja .. -DCMAKE_INSTALL_PREFIX=../install
ninja install
