# CODE

This is the ITPcp plasma group meta-repository that collects scripts
to setup development and use of internal and external codes.

## Prerequisites
On Windows: Prepare your machine with `scripts/setup_windows.bat` first.
`CODE` can then be used via the **devcontainer** or alternatively via
Debian Linux in WSL.

On Linux: At ITPcp all packages should be installed to get going.
For your own setup, look inside the packages in the
Dockerfiles `base`, `devel` and `devel-tex` inside `images` or
use the **devcontainer**.

On Mac: The recommended way is the **devcontainer**. The next option is
`multipass` via the most recent LTS version of Ubuntu Linux. The
option on the local system is `brew` with similar packages as in Linux,
but it is prone to problems with Fortran on Apple Silicon.

If you haven't done so earlier, set up your SSH keys in `~/.ssh` via `ssh-keygen` and
add the content of `id_rsa.pub` to Gitlab and GitHub for authentication.

## Installation

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
