#!/bin/bash

sudo --validate || exit 1
sudo apt update && sudo apt install -y --no-install-recommends llvm-dev

curl -L https://github.com/nlohmann/json/archive/refs/tags/v3.11.3.tar.gz -o - | tar xz
mkdir -p json-3.11.3/build
pushd json-3.11.3/build
cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=$CODE/.venv
make -j$(nproc)
make install
popd

curl -L https://github.com/jupyter-xeus/xeus/archive/refs/tags/5.1.0.tar.gz  -o - | tar xz
mkdir -p xeus-5.1.0/build
pushd xeus-5.1.0/build
cmake .. -D CMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=$CODE/.venv
make -j$(nproc)
make install
popd

curl -L https://github.com/jupyter-xeus/xeus-zmq/archive/refs/tags/3.0.0.tar.gz -o - | tar xz
mkdir -p xeus-zmq-3.0.0/build
pushd xeus-zmq-3.0.0/build
cmake .. -D CMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=$CODE/.venv
make -j$(nproc)
make install
popd

curl -L https://github.com/lfortran/lfortran/releases/download/v0.37.0/lfortran-0.37.0.tar.gz -o - | tar xz
mkdir -p lfortran-0.37.0/build
pushd lfortran-0.37.0/build
cmake .. -DCMAKE_BUILD_TYPE=Debug -DWITH_LLVM=yes -DCMAKE_INSTALL_PREFIX=$CODE/.venv -DWITH_XEUS=yes
make -j$(nproc)
make install
popd
