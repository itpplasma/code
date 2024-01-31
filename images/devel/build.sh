#!/usr/bin/env bash

ARCH=$(uname -m)

if  [ "$ARCH" == "x86_64" ] || [ "$ARCH" == "amd64" ]; then
    PLATFORM="linux/amd64"
elif [ "$ARCH" == "aarch64" ] || [ "$ARCH" == "arm64" ]; then
    PLATFORM="linux/arm64"
else
    echo "Unsupported architecture: $ARCH"
    exit 1
fi

docker build -t ghcr.io/itpplasma/devel --platform $PLATFORM .
