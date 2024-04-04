# CODE

## Getting Started

Clone the repository to your working copy, at the institute this is

    git clone git@gitlab.tugraz.at:plasma/code /proj/plasma/CODE/<username>

Then enter this directory and run

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
