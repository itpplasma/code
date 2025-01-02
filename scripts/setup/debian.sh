#!/bin/bash

# src: https://stackoverflow.com/a/246128/16527499
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# Enable non-free packages
sudo sed -i -e's/ main$/ main contrib non-free non-free-firmware/g' \
    /etc/apt/sources.list.d/debian.sources
sudo sed -i -e's/ main$/ main contrib non-free non-free-firmware/g' \
    /etc/apt/sources.list

sudo apt-get update -y && apt-get upgrade -y -q --no-install-recommends

sudo $SCRIPT_DIR/debian/base.sh
sudo $SCRIPT_DIR/debian/interactive.sh
sudo $SCRIPT_DIR/debian/libs.sh
sudo $SCRIPT_DIR/debian/octave.sh
sudo $SCRIPT_DIR/debian/texlive.sh
sudo $SCRIPT_DIR/debian/fonts.sh
