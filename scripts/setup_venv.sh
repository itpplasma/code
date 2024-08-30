#!/bin/bash

# We check explicitly for Python 3.11 since it's the last version
# supporting numpy.distutils, which is required by gacode for OMFIT.
if command -v python3.11 &> /dev/null; then
    PYTHON=$(command -v python3.11)
elif command -v python3 &> /dev/null; then
    PYTHON=$(command -v python3)
elif command -v python &> /dev/null; then
    PYTHON=$(command -v python)
else
    echo "Python not found in PATH."
    exit 1
fi

$PYTHON -m venv --system-site-packages .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install --upgrade meson
python -m pip install --upgrade ninja
python -m pip install -r requirements.txt
