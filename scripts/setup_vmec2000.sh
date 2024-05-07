
#!/bin/bash

if [ ! -d "VMEC2000" ] ; then
    echo "Fetching and building VMEC2000..."
    git clone https://github.com/hiddenSymmetries/VMEC2000.git
    pushd VMEC2000
    cmake -S. -Bbuild -GNinja -DNETCDF_INC_PATH=/usr/include -DSCALAPACK_PRE
FIX=$CODE/external/scalapack/build
    pushd build
    ninja
    popd
    popd
fi
