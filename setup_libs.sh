#!/bin/sh

# If libneo dir doesnt exist, clone it
if [ ! -d "libneo" ] ; then
    echo "Cloning 'libneo'..."
    scripts/clone_libneo.sh
fi

echo "Building and installing 'libneo'..."
cd libneo
./build.sh
cd python
python -m pip install -e .
