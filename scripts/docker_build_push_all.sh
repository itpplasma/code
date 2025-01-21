#!/usr/bin/env bash
$CODE/images/base/cross_build_push.sh
$CODE/images/devel/cross_build_push.sh
$CODE/images/devel-tex/cross_build_push.sh
$CODE/images/devcontainer/cross_build_push.sh

docker pull ghcr.io/itpplasma/devcontainer
