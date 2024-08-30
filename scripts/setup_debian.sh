#!/bin/bash

# Enable non-free packages
sudo sed -i -e's/ main$/ main contrib non-free non-free-firmware/g' \
    /etc/apt/sources.list.d/debian.sources
sudo sed -i -e's/ main$/ main contrib non-free non-free-firmware/g' \
    /etc/apt/sources.list

sudo apt-get update -y && apt-get upgrade -y -q --no-install-recommends

sudo $CODE/scripts/setup_base.sh
sudo $CODE/scripts/setup_interactive.sh
sudo $CODE/scripts/setup_libs.sh
sudo $CODE/scripts/setup_octave.sh
sudo $CODE/scripts/setup_texlive.sh
sudo $CODE/scripts/setup_fonts.sh
