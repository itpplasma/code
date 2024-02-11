#!/bin/bash

scripts/setup_venv.sh
source activate.sh

cd libs
../scripts/setup_fgsl.sh
../scripts/setup_mfem.sh
cd ..

scripts/setup_libneo.sh
