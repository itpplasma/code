#!/usr/bin/env bash

docker buildx build --platform linux/amd64,linux/arm64 -f $CODE/images/base/Dockerfile -t ghcr.io/itpplasma/base . --push
