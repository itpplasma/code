#!/usr/bin/env bash

docker buildx build --platform linux/amd64,linux/arm64 -t ghcr.io/itpplasma/base . --push
