# CODE

This is the ITPcp plasma group meta-repository that collects scripts
to setup development and use of internal and external codes. Our development
environment is Visual Studio Code, and we strongly recommend GitHub Copilot
there and in the [CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli/setting-up-github-copilot-in-the-cli).

CODE is based around our standard Debian bookworm system at ITPcp and provides 

- setup scripts `scripts/setup/...`
- quality-of-life shell commands via `scripts/util.sh`
- a standardized activation together with a Python virtual environment from `requirements.txt`
- CI/CD for integration between codes and data
- container definitions
- VSCode settings

## Getting Started

If you haven't done so earlier, set up your SSH keys in `~/.ssh` via `ssh-keygen` and
add the content of `id_rsa.pub` to Gitlab and GitHub for authentication.

### Installation at ITPcp computers

Clone the repository to your working copy, at the institute this is

    git clone git@github.com:itpplasma/code /proj/plasma/CODE/<username>

Then open the directory in VS Code with

    code code

When asked to initialize the devcontainer, remove the message. 
Run the setup script manually with

    source scripts/setup.sh

The setup will install external dependencies and create and activate
a Python virtual environment in the hidden `.venv` directory.

If you work outside a container, also manually put a line

    source /path/to/code/activate.sh

in your bashrc.


### Installation on your own machine

On Linux: At ITPcp all packages should be installed to get going.
The according script is [scripts/setup/debian.sh](scripts/setup/debian.sh).

On Mac: The recommended way via **orbstack**
and **devpod** as described in [scripts/setup/mac.sh](scripts/setup/mac.sh).

On Windows: Prepare your machine with
[scripts/setup/windows.bat](scripts/setup/windows.bat) first
to install Debian Linux via WSL2. Then follow the Linux instructions.


## Tests

Integration tests are run by

    pytest tests/

This will perform all the tests in `tests/` and its subfolders.


