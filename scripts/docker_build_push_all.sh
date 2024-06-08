#!/bin/bash
$CODE/images/base/cross_build_push.sh 
sleep 10
docker pull ghcr.io/itpplasma/base 
$CODE/images/devel/cross_build_push.sh
sleep 10
docker pull ghcr.io/itpplasma/devel
$CODE/images/devel-tex/cross_build_push.sh
docker pull ghcr.io/itpplasma/devel-tex
