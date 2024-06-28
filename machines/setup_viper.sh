#!/bin/bash

source $CODE/machines/init_viper.sh

mkdir -p $HOME/.local/lib
ln -s $MKL_PARTS_HOME/lib/libmkl_blas.so $HOME/.local/lib/libblas.so
ln -s $MKL_PARTS_HOME/lib/libmkl_lapack.so $HOME/.local/lib/liblapack.so
