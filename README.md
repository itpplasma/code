# CODE

This is the ITPcp plasma group meta-repository that collects scripts
to setup development and use of internal and external codes. Our development
environment is Visual Studio Code, and we strongly recommend GitHub Copilot
there and in the [CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli/setting-up-github-copilot-in-the-cli).

CODE is based around our standard Debian bookworm system at ITPcp and provides

- Setup scripts `scripts/setup/...`
- Quality-of-life shell commands via `scripts/util.sh`
- A standardized activation together with a Python virtual environment from `requirements.txt`
- CI/CD for integration between codes and data
- Container definitions
- VSCode settings

## Getting Started

If you haven't done so earlier, set up your SSH keys in `~/.ssh` via `ssh-keygen`
with a **blank passphrase** and add the content of `id_rsa.pub` to Gitlab and GitHub
for authentication.

### Perparing your machine

On Linux: At ITPcp computers all packages should be installed to get going.
On your own Debian system, run [scripts/setup/debian.sh](scripts/setup/debian.sh).

On Mac: The recommended way via **orbstack**
and **devpod** as described in [scripts/setup/mac.sh](scripts/setup/mac.sh).

On Windows: Prepare your machine with
[scripts/setup/windows.bat](scripts/setup/windows.bat) first
to install Debian Linux via WSL2. Then follow the Linux instructions.


### Initial setup

Clone the repository to your working copy, at the institute this is

    git clone git@github.com:itpplasma/code /proj/plasma/CODE/<username>

Then open the directory in VS Code with

    code code

When asked to initialize the devcontainer, remove the message.
Run the setup script manually with

    scripts/setup.sh

The setup will install external dependencies and create
a Python virtual environment in the hidden `.venv` directory.

Finally, activate the environment with

    source activate.sh

To use this environment as a standard, put the activation
script into bashrc with

    echo "source $PWD/activate.sh" >> ~/.bashrc

## External codes

### Intel compiler for codes GPEC and MARS
GPEC and MARS require the Intel toolchain with classic `ifort` to be installed. Run

    scripts/setup/compiler_intel.sh

to install the compiler and libraries HDF5 and NetCDFwith modules. Then the commands

    scripts/setup/gpec.sh
    scripts/setup/mars.sh

will install GPEC and MARS into `$CODE/external/intel`. They can then be loaded with

    module load gpec
    module load mars

to make dependencies and binaries available in the shell. Be careful not to work on
codes based on GNU Fortran in the same shell.

### OMFIT
OMFIT requires its own Python environment provided via `conda`. Both will be installed
to `$CODE/external` by running

    deactivate
    scripts/setup/omfit.sh

OMFIT can then be loaded with

    deactivate
    source $CODE/external/mambaforge/bin/activate omfit
    module load omfit

and then started with `omfit` in the shell. Be careful not to work on codes
based on the standard Python venv in the same shell.

## Testing

Integration tests are run by

    pytest tests/

This will perform all the tests in `tests/` and its subfolders.
