#!/bin/bash

FILES_TO_WRAP=../src/sparse_mod.f90
OBJECT_FILES=sparse_mod.o
F90WRAP_OUTPUT=f90wrap_sparse_mod.f90
F2PY_F2CMAP=../python/.f2py_f2cmap
export FFLAGS="-I`pwd`"

gfortran -fPIC -c $FILES_TO_WRAP
f90wrap -m sparse -k $F2PY_F2CMAP $FILES_TO_WRAP
f2py-f90wrap -c -m _sparse --f2cmap $F2PY_F2CMAP --lower $F90WRAP_OUTPUT libsparse.so
