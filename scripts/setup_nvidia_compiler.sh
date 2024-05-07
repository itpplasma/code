
#!/bin/bash

echo "Setting up NVIDIA compiler..."
curl https://developer.download.nvidia.com/hpc-sdk/ubuntu/DEB-GPG-KEY-NVIDIA-HPC-SDK | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-hpcsdk-archive-keyring.gpg
tar xpzf nvhpc_2024_243_Linux_aarch64_cuda_multi.tar.gz
nvhpc_2024_243_Linux_aarch64_cuda_multi/install
