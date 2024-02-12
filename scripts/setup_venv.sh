#!/bin/bash

# Check if 'python3' exists in the PATH
if command -v python3 &> /dev/null; then
    # 'python' is available, set the PYTHON variable to it
    PYTHON=$(command -v python3)
else
    # 'python3' is not available, so try 'python'
    if command -v python &> /dev/null; then
        # 'python3' is available, set the PYTHON variable to it
        PYTHON=$(command -v python)
    else
        echo "Python not found in PATH."
        exit 1
    fi
fi

$PYTHON -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
