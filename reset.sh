#!/bin/bash

cd $( dirname "${BASH_SOURCE[0]}" )

deactivate
rm -Rf .venv

rm -Rf libneo
rm -Rf efit_to_boozer

rm -Rf external/fgsl-1.5.0

unset CODE
