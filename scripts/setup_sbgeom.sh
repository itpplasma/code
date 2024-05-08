
#!/bin/bash
#
# Setup for SBGeom - A simple package for creating blanket geometries
# derived from Fourier Surfaces. Package author: Timo Bogaarts, TU Eindhoven
#

if [ ! -d "SBGeom-main" ] ; then
    echo "Extracting SBGeom-main..."
    unzip SBGeom-main.zip
fi

pushd SBGeom-main
git clone https://github.com/pybind/pybind11
git clone https://gitlab.com/libeigen/eigen.git
export EIGEN3_INCLUDE_DIR=$PWD/eigen/
cmake -DCMAKE_BUILD_TYPE=Release -S . -B build/
pushd build
make -j 12
cp *.so ../SBGeom
popd
pip install -e .
popd
