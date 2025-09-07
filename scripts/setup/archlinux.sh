#!/usr/bin/env bash

set -euo pipefail

# Update the system
pacman -Syu --noconfirm

# Install basic packages (skip already installed ones)
pacman -S --noconfirm --needed \
    file \
    sudo \
    unzip \
    ca-certificates \
    curl \
    rsync \
    wget \
    git \
    git-lfs \
    gnupg \
    gcc-fortran \
    ninja \
    cmake \
    python \
    python-pip \
    python-virtualenv \
    python-setuptools \
    openssh \
    base-devel \
    openssl \
    gettext \
    bash-completion \
    gcovr \
    lcov

# Install additional tools (skip already installed ones)
pacman -S --noconfirm --needed \
    procps-ng \
    nano \
    vim \
    man-db \
    stow \
    htop \
    ncdu \
    less \
    gdb \
    valgrind \
    dbus \
    findutils \
    mc \
    tree \
    ripgrep \
    bc \
    perl-image-exiftool \
    qpdf \
    kitty-terminfo

# Install development libraries (skip already installed ones)
pacman -S --noconfirm --needed \
    pkgconf \
    libtool \
    suitesparse \
    openblas \
    superlu \
    hdf5 \
    netcdf \
    netcdf-fortran \
    fftw \
    gsl \
    openmpi \
    pcre \
    readline \
    boost-libs \
    boost \
    pugixml \
    zstd \
    cppzmq \
    util-linux \
    graphviz \
    docker \
    docker-compose

# Install TeX Live and related tools (skip already installed ones)
pacman -S --noconfirm --needed \
    texlive-core \
    texlive-bin \
    texlive-latex \
    texlive-latexextra \
    texlive-publishers \
    texlive-science \
    texlive-bibtexextra \
    texlive-luatex \
    texlive-binextra \
    texlive-fontsextra \
    texlive-langgreek \
    biber \
    ghostscript \
    doxygen \
    poppler \
    imagemagick

# Install fonts (skip already installed ones)
pacman -S --noconfirm --needed \
    noto-fonts-cjk \
    ttf-inconsolata \
    ttf-linux-libertine \
    tex-gyre-fonts
