#!/bin/bash
cd external
echo "Building Slatec..."
mkdir slatec
cd slatec
mkdir lib
curl -L https://www.netlib.org/slatec/slatec_src.tgz -o - | tar xz

# Check the operating system
if [[ "$(uname)" == "Darwin" ]]; then
    # macOS specific compiler flags
    CFLAGS="-O2"
elif [[ "$(uname)" == "Linux" ]]; then
    # Linux specific compiler flags
    CFLAGS="-msse2 -mfpmath=sse"
else
    # Default compiler flags for other operating systems
    CFLAGS=""
fi
gfortran -c -Wall -Wtabs -mtune=generic $CFLAGS src/*.f
ar rcs libslatec.a *.o
rm *.o

echo "Finished building Slatec..."

echo ""
echo "Building Sundials..."
cd ..
git clone git@github.com:LLNL/sundials.git
cd sundials 
git checkout v5.7.0
mkdir build
cd build
cmake ..
make
echo "Finished building Sundials..."


cd ..
cd ..

mkdir bessel
cd bessel
cp -r ../../KiLCA/math/bessel/* .
cd slatec
gfortran -c -Wall -Wtabs -mtune=generic -msse2 -mfpmath=sse *.f
ar rcs libbessel.a *.o
rm *.o
mv libbessel.a ../lib/
cd ..
cd ..

echo "Building lapack..."
curl -L http://www.netlib.org/lapack/lapack-3.2.1.tgz -o - | tar xz
cd lapack-3.2.1
cp INSTALL/make.inc.gfortran make.inc
make lib
make blaslib
make clean

echo "Finished building lapack..."

echo "Building gsl-2.4"
cd ..
curl -L https://ftp.gnu.org/gnu/gsl/gsl-2.4.tar.gz -o - | tar xz
cd gsl-2.4
./configure
make
echo "Finished building gsl-2.4..."

wget https://portal.nersc.gov/project/sparse/superlu/superlu_4.1.tar.gz
tar -xzvf superlu_4.1.tar.gz
rm superlu_4.1.tar.gz
cd SuperLU_4.1


cd ..
cd ..

cd KiLCA-QB/KiLCA
mkdir build
cd build
cmake ..
make

cd ../ql-balance/
mkdir build
cd build
cmake ..
make
