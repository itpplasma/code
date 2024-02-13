#!/bin/bash

scripts/setup_venv.sh
source activate.sh

cd libs
../scripts/setup_fgsl.sh
cd ..

scripts/setup_libneo.sh
