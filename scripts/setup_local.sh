#!/bin/bash

# Enable non-free packages
sed -i -e's/ main$/ main contrib non-free non-free-firmware/g' \
    /etc/apt/sources.list.d/debian.sources
sed -i -e's/ main$/ main contrib non-free non-free-firmware/g' \
    /etc/apt/sources.list

apt-get update -y && apt-get upgrade -y -q --no-install-recommends

bash setup_base.sh
bash setup_interactive.sh
bash setup_libs.sh
bash setup_octave.sh
bash setup_python.sh
bash setup_texlive.sh
bash setup_fonts.sh
