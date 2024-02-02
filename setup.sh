#!/bin/bash

scripts/setup_venv.sh
source activate.sh

cd libs
../scripts/setup_fgsl.sh
../scripts/setup_mfem.sh
../scripts/setup_libneo.sh
cd ..
