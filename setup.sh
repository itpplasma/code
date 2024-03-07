#!/bin/bash

source scripts/setup_venv.sh

cd external
../scripts/setup_fgsl.sh
cd ..

scripts/setup_libneo.sh
