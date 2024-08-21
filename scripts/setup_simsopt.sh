
#!/bin/bash

if [ ! -d "simsopt" ] ; then
    echo "Fetching and building simsopt..."
    git clone git@github.com:hiddenSymmetries/simsopt.git
fi

pushd simsopt
pip install --no-build-isolation -e .
popd
