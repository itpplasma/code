#!/bin/bash

# Manually deactivate any existing venv
if [[ -n "$VIRTUAL_ENV" ]]; then
    PATH=${PATH//:$VIRTUAL_ENV\/bin/}  # Remove the venv's bin directory from PATH
    unset VIRTUAL_ENV
    # Reset the prompt to its original state if it was changed by the venv
    if [[ -n "${_OLD_VIRTUAL_PS1+set}" ]]; then
        PS1="$_OLD_VIRTUAL_PS1"
        unset _OLD_VIRTUAL_PS1
    fi
fi

puts stdout "/temp/AG-plasma/opt/mambaforge/bin/activate omfit;"
