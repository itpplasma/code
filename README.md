# CODE

## Getting Started

    source setup.sh

This will install library dependencies in `libs` and create and activate
a Python virtual environment in the hidden `.venv` directory.

Then start Visual Studio Code via

    code .

Put a line

    source /path/to/code/activate.sh

in your bashrc.

## Tests

Tests can currently be performed by using

    pytest tests/

This will perform all the tests in `tests/` and its subfolders.
