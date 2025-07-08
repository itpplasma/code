#!/usr/bin/env bash

# Update the system
dnf update -y

# Install basic packages (skip already installed ones)
dnf install -y \
    file \
    sudo \
    unzip \
    ca-certificates \
    curl \
    rsync \
    wget \
    git \
    git-lfs \
    gnupg2 \
    gcc-gfortran \
    ninja-build \
    cmake \
    python3 \
    python3-pip \
    python3-virtualenv \
    python3-setuptools \
    openssh \
    gcc \
    gcc-c++ \
    make \
    openssl \
    openssl-devel \
    gettext \
    bash-completion

# Install additional tools (skip already installed ones)
dnf install -y \
    procps-ng \
    nano \
    vim \
    man-db \
    stow \
    bash-completion \
    htop \
    ncdu \
    less \
    gdb \
    cmake \
    valgrind \
    dbus \
    kcachegrind \
    tk \
    gdb \
    findutils

# Install development libraries (skip already installed ones)
dnf install -y \
    pkgconf \
    libtool \
    suitesparse \
    suitesparse-devel \
    openblas \
    openblas-devel \
    SuperLU \
    SuperLU-devel \
    hdf5 \
    hdf5-devel \
    netcdf \
    netcdf-devel \
    netcdf-fortran \
    netcdf-fortran-devel \
    fftw \
    fftw-devel \
    gsl \
    gsl-devel \
    openmpi \
    openmpi-devel \
    pcre \
    pcre-devel \
    readline \
    readline-devel \
    boost \
    boost-devel \
    pugixml \
    pugixml-devel \
    zstd \
    zstd-devel \
    cppzmq-devel \
    util-linux \
    graphviz \
    graphviz-devel

# Install PETSc, SLEPc, and related packages
dnf install -y \
    metis \
    metis-devel \
    parmetis \
    parmetis-devel \
    scotch \
    scotch-devel \
    petsc \
    petsc-devel \
    slepc \
    slepc-devel \
    scalapack \
    scalapack-devel

# Note: triangle package might need to be compiled from source or found in COPR repos
# For triangle, you may need to:
# 1. Enable a COPR repository that provides it, or
# 2. Build from source: https://www.cs.cmu.edu/~quake/triangle.html

# Install Octave and gnuplot (skip already installed ones)
dnf install -y octave gnuplot

# Install TeX Live and related tools (skip already installed ones)
dnf install -y \
    texlive-scheme-full \
    texlive-latex \
    texlive-latexextra \
    texlive-publishers \
    texlive-science \
    texlive-bibtex \
    texlive-luatex \
    biber \
    ghostscript \
    doxygen \
    poppler \
    poppler-utils \
    ImageMagick

# Install additional LaTeX tools
dnf install -y \
    latexmk \
    lyx \
    dvipng \
    texlive-lm

if [ -n "$(ls /tmp/fmt* 2>/dev/null)" ]; then
  cat /tmp/fmt*
fi

# Install fonts (skip already installed ones)
dnf install -y \
    texlive-tex-gyre \
    google-noto-cjk-fonts \
    levien-inconsolata-fonts \
    linux-libertine-fonts