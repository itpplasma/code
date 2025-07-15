#!/usr/bin/env zsh

brew install gcc make cmake ninja python python-tk
brew install gsed autoconf automake autogen git lazygit
brew install openblas suitesparse metis gsl
brew install hdf5 netcdf netcdf-fortran fftw graphviz
brew install node vim tmux htop wget gcovr lcov
brew install modules tree mc ripgrep bc

echo "source /opt/homebrew/opt/modules/init/zsh" >> $HOME/.zshrc
