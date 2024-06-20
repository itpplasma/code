#!/bin/bash

# Enable non-free packages
sed -i -e's/ main$/ main contrib non-free non-free-firmware/g' \
    /etc/apt/sources.list.d/debian.sources
sed -i -e's/ main$/ main contrib non-free non-free-firmware/g' \
    /etc/apt/sources.list

apt-get update -y && apt-get upgrade -y -q --no-install-recommends

bash base/setup_base.sh
bash devel/setup_interactive.sh
bash devel/setup_libs.sh
bash devel/setup_octave.sh
bash devel/setup_python.sh
bash devel-tex/setup_texlive.sh
bash devel-tex/setup_fonts.sh
