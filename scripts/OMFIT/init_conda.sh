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

__conda_setup="$('/temp/AG-plasma/opt/mambaforge/bin/conda' 'shell.bash' 'hook' 2> /dev/null)"
if [ $? -eq 0 ]; then
    eval "$__conda_setup"
else
    if [ -f "/AG-plasma/opt/mambaforge/etc/profile.d/conda.sh" ]; then
        . "/temp/AG-plasma/opt/mambaforge/etc/profile.d/conda.sh"
    else
        export PATH="/temp/AG-plasma/opt/mambaforge/bin:$PATH"
    fi
fi
unset __conda_setup

if [ -f "/temp/AG-plasma/opt/mambaforge/etc/profile.d/mamba.sh" ]; then
    . "/temp/AG-plasma/opt/mambaforge/etc/profile.d/mamba.sh"
fi

mamba activate omfit
