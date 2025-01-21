#!/usr/bin/env bash

cd $( dirname "${BASH_SOURCE[0]}" )

deactivate
rm -Rf .venv

rm -Rf libneo

rm -Rf external/fgsl-1.6.0

unset CODE
