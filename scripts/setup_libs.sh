#!/bin/sh

cd libs

if [ ! -d "fgsl-1.5.0" ] ; then
    echo "Fetching and building FGSL..."
    wget https://doku.lrz.de/files/10746505/10746508/1/1684600947173/fgsl-1.5.0.tar.gz
    tar xzvf fgsl-1.5.0.tar.gz
    cd fgsl-1.5.0
    ./configure
    make
    cd ..
fi


if [ ! -d "mfem-4.6" ] ; then
    echo "Fetching and building MFEM..."
    wget https://bit.ly/mfem-4-6 -O mfem-4.6.tgz
    tar xzvf mfem-4.6.tgz
    cd mfem-4.6
    mkdir build
    cd build
    cmake .. -DMFEM_USE_SUITESPARSE=1
    make -j4
    cd ../..
fi


if [ ! -d "libneo" ] ; then
    echo "Cloning 'libneo'..."
    ../scripts/clone_github.sh libneo
fi

echo "Building and installing 'libneo'..."
cd libneo
./build.sh
cd python
python -m pip install -e .
