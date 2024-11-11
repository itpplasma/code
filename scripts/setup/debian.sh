#!/bin/bash

# Enable non-free packages
sudo sed -i -e's/ main$/ main contrib non-free non-free-firmware/g' \
    /etc/apt/sources.list.d/debian.sources
sudo sed -i -e's/ main$/ main contrib non-free non-free-firmware/g' \
    /etc/apt/sources.list

sudo apt-get update -y && apt-get upgrade -y -q --no-install-recommends

sudo $CODE/scripts/setup/linux/base.sh
sudo $CODE/scripts/setup/linux/interactive.sh
sudo $CODE/scripts/setup/linux/libs.sh
sudo $CODE/scripts/setup/linux/octave.sh
sudo $CODE/scripts/setup/linux/texlive.sh
sudo $CODE/scripts/setup/linux/fonts.sh
