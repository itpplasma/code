#!/bin/bash

source scripts/setup_venv.sh

cd libs
../scripts/setup_fgsl.sh
cd ..

scripts/setup_libneo.sh
