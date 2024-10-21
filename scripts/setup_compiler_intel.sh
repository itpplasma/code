#!/bin/bash

echo "Setting up Intel compiler..."
sudo --validate || exit 1

# download the key to system keyring
wget -O- https://apt.repos.intel.com/intel-gpg-keys/GPG-PUB-KEY-INTEL-SW-PRODUCTS.PUB \
    | gpg --dearmor \
    | sudo tee /usr/share/keyrings/oneapi-archive-keyring.gpg > /dev/null

# add signed entry to apt sources and configure the APT client to use Intel repository:
echo "deb [signed-by=/usr/share/keyrings/oneapi-archive-keyring.gpg] https://apt.repos.intel.com/oneapi all main" \
    | sudo tee /etc/apt/sources.list.d/oneAPI.list

sudo apt-get update  --quiet --quiet
sudo apt-get install --quiet --yes --no-install-recommends intel-basekit intel-hpckit

# create configuration for "environment modules"
if [ -x /opt/intel/oneapi/modulefiles-setup.sh ] ; then
    if [ -d /usr/share/modules/modulefiles ] ; then
        /opt/intel/oneapi/modulefiles-setup.sh \
            --force --ignore-latest \
            --output-dir=/usr/share/modules/modulefiles/intel
    else
        echo "## ENVIRONMENT Modules - where is the config directory?"
    fi
else
    echo "## ENVIRONMENT Modules - don't know how to configure!"
fi