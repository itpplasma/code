#!/usr/bin/env bash
cd external
echo "Building Slatec..."
mkdir slatec
cd slatec
mkdir lib
curl -L https://www.netlib.org/slatec/slatec_src.tgz -o - | tar xz

CFLAGS="-O2"
gfortran -c -Wall -Wtabs -mtune=generic $CFLAGS src/*.f
ar rcs libslatec.a *.o
rm *.o
mv libslatec.a ../lib/
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
cd ../../
echo "Finished building Sundials..."

mkdir bessel
cd bessel
cp -r ../../KAMEL/KiLCA/math/bessel/* .
cd slatec
gfortran -c -Wall -Wtabs -mtune=generic $CFLAGS *.f
ar rcs libbessel.a *.o
rm *.o
mv libbessel.a ../../lib/
cd ../../

echo "Building lapack..."
curl -L http://www.netlib.org/lapack/lapack-3.2.1.tgz -o - | tar xz
cd lapack-3.2.1
cp INSTALL/make.inc.gfortran make.inc
make lib
make blaslib
make clean
cd ..
echo "Finished building lapack..."

echo "Building gsl-2.4"
curl -L https://ftp.gnu.org/gnu/gsl/gsl-2.4.tar.gz -o - | tar xz
cd gsl-2.4
./configure
make
cd ..
echo "Finished building gsl-2.4..."

wget https://portal.nersc.gov/project/sparse/superlu/superlu_4.1.tar.gz
tar -xzvf superlu_4.1.tar.gz
rm superlu_4.1.tar.gz
cd SuperLU_4.1
cp MAKE_INC/make.linux make.inc


# Check the operating system
if [[ "$(uname)" == "Darwin" ]]; then
    # macOS specific compiler flags
    CFLAGS="-O2"
    CFLAGS+=" -I/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include"
    sed -E "s|SuperLUroot\t=.*|SuperLUroot\t= $(pwd)|g" make.inc >> make.inc.tmp
    mv make.inc.tmp make.inc
    sed -E "s|BLASLIB[[:space:]]*=.*|BLASLIB = #-lblas|g" make.inc >> make.inc.tmp
    mv make.inc.tmp make.inc
    sed -E "s|RANLIB       = ranlib|RANLIB       = gcc-ranlib-12|g" make.inc >> make.inc.tmp
    mv make.inc.tmp make.inc
    sed -E "s|CC           = gcc|CC           = gcc-12|g" make.inc >> make.inc.tmp
    mv make.inc.tmp make.inc
    sed -E "s|ARCH         = ar|ARCH         = gcc-ar-12|g" make.inc >> make.inc.tmp
    mv make.inc.tmp make.inc
    sed -E "s|CFLAGS       = -O3|CFLAGS       = -O3 -I/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include|g" make.inc >> make.inc.tmp
    mv make.inc.tmp make.inc
elif [[ "$(uname)" == "Linux" ]]; then
    # Linux specific compiler flags
    sed -i "s|SuperLUroot\t= \$(HOME)/Codes/SuperLU_4.1|SuperLUroot = $(pwd)|" make.inc
    sed -i 's/g77/gfortran/g' make.inc
fi

make

cd ..
cd ..

cd KAMEL/KiLCA
mkdir build
cd build
cmake ..
make

cd ../../KIM/
mkdir build
cd build
cmake ..
make

cd ../../QL-Balance/
mkdir build
cd build
cmake ..
make
