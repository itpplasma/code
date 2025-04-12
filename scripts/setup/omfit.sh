#!/usr/bin/env bash

echo "Setting up OMFIT..."
echo "Requires personal Git access to OMFIT repository."
echo "Code access: https://omfit.io/install.html"

deactivate

export USER=`whoami`
cd $CODE/external
git clone --filter=blob:none -b unstable git@gitlab.tugraz.at:plasma/codes/OMFIT-source.git
cd OMFIT-source
git submodule update --init omas
install/install.sh
mkdir -p $HOME/.LICENSES
cp $CODE/scripts/setup/omfit/LICENSES/* $HOME/.LICENSES/
