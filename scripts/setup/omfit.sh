#!/usr/bin/env bash

echo "Setting up OMFIT..."
echo "Requires personal Git access to OMFIT repository."
echo "Code access: https://omfit.io/install.html"

deactivate

export USER=`whoami`
git clone --filter=blob:none --recursive -b unstable git@gitlab.tugraz.at:plasma/codes/OMFIT-source.git
OMFIT-source/install/install.sh
mkdir -p $HOME/.LICENSES
cp $CODE/scripts/setup/omfit/LICENSES/* $HOME/.LICENSES/
