#!/bin/bash
cd base 
./cross_build_push.sh 
sleep 10
docker pull ghcr.io/itpplasma/base
cd ../devel 
./cross_build_push.sh
sleep 10
docker pull ghcr.io/itpplasma/devel
cd ../devel-tex
./cross_build_push.sh
docker pull ghcr.io/itpplasma/devel-tex
