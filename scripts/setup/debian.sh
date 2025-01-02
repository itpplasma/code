#!/bin/bash
set -e

# src: https://stackoverflow.com/a/246128/16527499
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

sudo apt-get update -y && sudo apt-get upgrade -y -q --no-install-recommends

sudo $SCRIPT_DIR/debian/base.sh
sudo $SCRIPT_DIR/debian/interactive.sh
sudo $SCRIPT_DIR/debian/libs.sh
sudo $SCRIPT_DIR/debian/octave.sh
sudo $SCRIPT_DIR/debian/texlive.sh
sudo $SCRIPT_DIR/debian/fonts.sh
