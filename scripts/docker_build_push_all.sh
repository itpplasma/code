#!/usr/bin/env bash
$INFRA/images/base/cross_build_push.sh
$INFRA/images/devel/cross_build_push.sh
$INFRA/images/devel-tex/cross_build_push.sh
$INFRA/images/devcontainer/cross_build_push.sh

docker pull ghcr.io/itpplasma/devcontainer
