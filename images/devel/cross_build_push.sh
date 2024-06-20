#!/usr/bin/env bash
docker pull ghcr.io/itpplasma/base
docker buildx build --platform linux/amd64,linux/arm64 -f $CODE/images/devel/Dockerfile -t ghcr.io/itpplasma/devel . --push
