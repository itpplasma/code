#!/usr/bin/env bash

set -euo pipefail

# Arch Linux desktop UI setup
# Excludes: google-chrome, thunar, pcmanfm, dolphin

# Update system
pacman -Syu --noconfirm

# GUI/desktop applications and tools
# These were separated from scripts/setup/archlinux.sh to keep
# CLI development tools and desktop UI tooling decoupled.
pacman -S --noconfirm --needed \
    kcachegrind \
    tk \
    octave \
    gnuplot \
    gimp \
    shutter \
    discord \
    kitty \
    code \
    totem

echo "Desktop UI packages installed (excluding chrome/thunar/pcmanfm/dolphin)."
