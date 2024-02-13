#!/bin/bash

if [ ! -d "libneo" ] ; then
    echo "Cloning 'libneo'..."
    scripts/clone_github.sh libneo
fi

# TODO: Integrate into libneo
if [ ! -d "efit_to_boozer" ] ; then
    echo "Cloning 'efit_to_boozer'..."
    scripts/clone_github.sh efit_to_boozer
fi

echo "Building and installing 'libneo'..."
cd libneo
./build.sh
cd python
python -m pip install -e .
