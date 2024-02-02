#!/bin/bash

if [ ! -d "libneo" ] ; then
    echo "Cloning 'libneo'..."
    ../scripts/clone_github.sh libneo
fi

echo "Building and installing 'libneo'..."
cd libneo
./build.sh
cd python
python -m pip install -e .
