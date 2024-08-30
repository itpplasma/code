#!/bin/bash

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
    bash-completion

#!/bin/bash

# Install additional tools (skip already installed ones)
pacman -S --noconfirm --needed \
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

#!/bin/bash

# Install development libraries (skip already installed ones)
pacman -S --noconfirm --needed \
    pkgconf \
    libtool \
    suitesparse \
    openblas \
    superlu \
    hdf5 \
    netcdf \
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
    util-linux

# The following packages need to be installed from the AUR:
# - metis
# - parmetis
# - scotch
# - petsc
# - slepc
# - scalapack
# - triangle-bin
# - hdf5-tools (tools are included in hdf5, additional ones might be in `hdf5-cpp-fortran`)
# - netcdf-tools (covered by netcdf, or install `netcdf-fortran`)

# Use yay or another AUR helper to install these:
# yay -S metis parmetis scotch petsc slepc scalapack triangle-bin hdf5-cpp-fortran netcdf-fortran

#!/bin/bash

# Install Octave and gnuplot (skip already installed ones)
pacman -S --noconfirm --needed octave gnuplot

#!/bin/bash

# Install TeX Live and related tools (skip already installed ones)
pacman -S --noconfirm --needed \
    texlive-core \
    texlive-latexextra \
    texlive-publishers \
    texlive-science \
    texlive-bibtexextra \
    texlive-luatex \
    biber \
    ghostscript \
    doxygen \
    poppler \
    imagemagick

# The following packages need to be installed from the AUR:
# - latexmk
# - lyx
# - dvipng
# - lmodern

# Use yay to install these:
# yay -S latexmk lyx dvipng lmodern

if [ -n "$(ls /tmp/fmt* 2>/dev/null)" ]; then
  cat /tmp/fmt*
fi

#!/bin/bash

# Install fonts (skip already installed ones)
pacman -S --noconfirm --needed \
    texlive-fontsextra \
    noto-fonts-cjk \
    ttf-inconsolata \
    ttf-linux-libertine \
    tex-gyre-fonts
