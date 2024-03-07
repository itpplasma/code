#!/bin/bash

module use -a $CODE/modules

if [ -n "$VIRTUAL_ENV" ]; then
    deactivate
fi

source .venv/bin/activate

