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

cat <<EOF

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
You might consider to insert the line
   "module use -a /opt/nvidia/hpc_sdk/modulefiles"
to your shell initialization file (e.g. ~/.bashrc).
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
EOF
