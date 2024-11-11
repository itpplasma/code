#!/bin/bash

echo "Setting up NVIDIA compiler..."

ARCH=$(dpkg --print-architecture)
sudo --validate || exit 1

curl https://developer.download.nvidia.com/hpc-sdk/ubuntu/DEB-GPG-KEY-NVIDIA-HPC-SDK \
    | gpg --dearmor \
    | sudo tee /usr/share/keyrings/nvidia-hpcsdk-archive-keyring.gpg > /dev/null
echo "deb [signed-by=/usr/share/keyrings/nvidia-hpcsdk-archive-keyring.gpg] https://developer.download.nvidia.com/hpc-sdk/ubuntu/$ARCH /" \
    | sudo tee /etc/apt/sources.list.d/nvhpc.list

sudo apt-get update  --quiet --quiet
sudo apt-get install --quiet --yes nvhpc-24-5

# create configuration for "environment modules"
if [ -d /opt/nvidia/hpc_sdk/modulefiles/ ] ; then
    if [ -d /usr/share/modules/modulefiles/ ] ; then
        ln --symbolic --force --verbose /opt/nvidia/hpc_sdk/modulefiles/* \
           --target-directory=/usr/share/modules/modulefiles/
        rm -rf /usr/share/modules/modulefiles/nvidia
    else
        echo "## ENVIRONMENT Modules - where is the config directory?"
    fi
else
    echo "## ENVIRONMENT Modules - don't know how to configure!"
fi
