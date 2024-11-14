#!/usr/bin/env bash
docker pull ghcr.io/itpplasma/devcontainer
docker buildx build --platform linux/amd64,linux/arm64 -f $CODE/images/devcontainer/Dockerfile -t ghcr.io/itpplasma/devcontainer . --push
