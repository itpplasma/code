
#!/bin/bash

if [ ! -d "VMEC2000" ] ; then
    echo "Fetching and building VMEC2000..."
    git clone https://github.com/hiddenSymmetries/VMEC2000.git
fi

pushd VMEC2000
cp $CODE/scripts/cmake_config_file_vmec2000.json cmake_config_file.json
sed -i "s|{CODE}|$CODE|g" cmake_config_file.json
cmake -S. -Bbuild -GNinja -DNETCDF_INC_PATH=/usr/include -DSCALAPACK_LIB_NAME=libscalapack-openmpi.so -DBLA_VENDOR=OpenBLAS
pushd build
ninja
popd
pip install --upgrade numpy f90wrap scikit-build
pip install .
popd
