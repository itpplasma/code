#!/usr/bin/env bash
docker pull ghcr.io/itpplasma/devel
docker buildx build --platform linux/amd64,linux/arm64 -f $CODE/images/devel-tex/Dockerfile -t ghcr.io/itpplasma/devel-tex . --push
