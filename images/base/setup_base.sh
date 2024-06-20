#!/bin/bash

apt-get update && apt-get install -y -q --no-install-recommends \
    file \
    sudo \
    environment-modules \
    unzip \
    apt-transport-https \
    build-essential \
    ca-certificates \
    curl \
    libssl-dev \
    locales \
    openssh-client \
    rsync \
    wget \
    git \
    gfortran \
    ninja-build \
    cmake \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv
