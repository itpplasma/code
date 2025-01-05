#!/bin/bash

if command -v python3 &> /dev/null; then
    PYTHON=$(command -v python3)
elif command -v python &> /dev/null; then
    PYTHON=$(command -v python)
else
    echo "Python not found in PATH."
    exit 1
fi

$PYTHON -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install --upgrade meson
python -m pip install --upgrade ninja
python -m pip install --upgrade -r requirements.txt
