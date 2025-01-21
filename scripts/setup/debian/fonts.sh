#!/usr/bin/env bash

apt-get install -y -q --no-install-recommends \
    texlive-fonts-recommended \
    fonts-cmu \
    fonts-inconsolata \
    fonts-linuxlibertine \
    fonts-lmodern \
    fonts-texgyre \
    fonts-texgyre-math

fc-cache -f -v
