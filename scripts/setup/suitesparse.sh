#!/bin/sh

cd $CODE/external
git clone https://github.com/DrTimothyAldenDavis/SuiteSparse
cd SuiteSparse/build
cmake -GNinja .. -DCMAKE_INSTALL_PREFIX=../install
ninja install
