#!/bin/bash
cd libs
echo "Building Slatec..."
mkdir slatec
cd slatec
mkdir lib
wget https://www.netlib.org/slatec/slatec_src.tgz
tar -xzvf slatec_src.tgz
rm slatec_src.tgz

gfortran -c -Wall -Wtabs -mtune=generic -msse2 -mfpmath=sse src/*.f
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

# not sure yet if needed
#mkdir bessel
#cd bessel
#cp -r ../../KiLCA/math/bessel/* .
#cd slatec
#gfortran -c -Wall -Wtabs -mtune=generic -msse2 -mfpmath=sse *.f
#ar rcs libbessel.a *.o
#rm *.o
#mv libbessel.a ../lib/
#cd ..
#cd ..

echo "Building lapack..."
wget http://www.netlib.org/lapack/lapack-3.2.1.tgz
tar -xzvf lapack-3.2.1.tgz
rm lapack-3.2.1.tgz
cd lapack-3.2.1
cp INSTALL/make.inc.gfortran make.inc
make lib
make blaslib
make clean

echo "Finished building lapack..."

echo "Building gsl-2.4"
cd ..
wget https://ftp.gnu.org/gnu/gsl/gsl-2.4.tar.gz
tar -xzvf gsl-2.4.tar.gz
rm gsl-2.4.tar.gz
cd gsl-2.4
./configure
make
echo "Finished building gsl-2.4..."

cd ..
cd ..

cd KiLCA
mkdir build
cd build
cmake ..
make