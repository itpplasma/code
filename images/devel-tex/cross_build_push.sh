#!/usr/bin/env bash
docker buildx build --platform linux/amd64,linux/arm64 $CODE/images/devel-tex/Dockerfile -t ghcr.io/itpplasma/devel-tex -f . --push
