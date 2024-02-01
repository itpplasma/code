#!/usr/bin/env bash

docker buildx create --name multi-arch \
	  --platform "linux/arm64,linux/amd64,linux/arm/v7" \
	  --driver "docker-container"

docker buildx use multi-arch
