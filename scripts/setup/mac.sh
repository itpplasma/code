#!/usr/bin/env zsh

brew install gcc make cmake ninja python python-tk
brew install gsed autoconf automake autogen git lazygit
brew install open-mpi hdf5-mpi netcdf netcdf-fortran fftw graphviz
brew install openblas suitesparse metis gsl petsc swig
brew install node vim tmux htop wget gcovr lcov
brew install modules tree mc ripgrep bc
brew install texlive poppler

echo "source /opt/homebrew/opt/modules/init/zsh" >> $HOME/.zshrc
