# CODE

This is the ITPcp plasma group meta-repository that collects scripts
to setup development and use of internal and external codes. Our development
environment is Visual Studio Code, and we strongly recommend GitHub Copilot
there and in the [CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli/setting-up-github-copilot-in-the-cli).

CODE supports the standard Debian system at ITPcp and Ubuntu LTS development
VMs, and provides

- Setup scripts `scripts/setup/...`
- Quality-of-life shell commands via `scripts/util.sh`
- A standardized activation together with a Python virtual environment from `requirements.txt`
- CI/CD for integration between codes and data
- Container definitions
- VSCode settings

## Layout

This repository is the `infra` directory of a workspace. The workspace is the
environment variable `$CODE`; this repository is `$INFRA`, equal to
`$CODE/infra`. The code checkouts sit in the workspace next to `infra`:

    $CODE/
      infra/      this repository (activation, setup scripts, modules, .venv)
      external/   prebuilt third-party libs, shared by all codes
      libneo/     code checkout
      SIMPLE/     code checkout
      ...

Activation exports both. `$INFRA` holds the infra-private paths (`scripts`,
`.venv`, `modules`). `$CODE` is the workspace root, so the codes resolve their
dependencies as `$CODE/<name>` and read prebuilt libraries from
`$CODE/external`.

## Getting Started

Authenticate GitHub and GitLab with their normal CLIs, or create a protected SSH
key and add its public key to the service. Do not put credentials into this
repository or a cloud-init file.

### Perparing your machine

On Linux: At ITPcp computers all packages should be installed to get going.
On your own Debian or Ubuntu system, install the complete scientific workstation
with:

    sudo scripts/setup/apt.sh full

The old `scripts/setup/debian.sh` command remains a compatibility wrapper.

On Mac: The recommended way via **orbstack**
and **devpod** as described in [scripts/setup/mac.sh](scripts/setup/mac.sh).

On Windows: Prepare your machine with
[scripts/setup/windows.bat](scripts/setup/windows.bat) first
to install Debian Linux via WSL2. Then follow the Linux instructions.


### Initial setup

Clone this repository into an `infra` directory inside your workspace. At the
institute the workspace is `/proj/plasma/CODE/<username>`:

    git clone git@github.com:itpplasma/code /proj/plasma/CODE/<username>/infra

Open the workspace in VS Code with

    code /proj/plasma/CODE/<username>

When asked to initialize the devcontainer, remove the message.
Run the setup script manually with

    infra/scripts/setup.sh

The setup installs external dependencies and creates the Python virtual
environment in `infra/.venv`.

Finally, activate the environment with

    source infra/activate.sh

To use this environment as a standard, put the activation
script into bashrc with

    echo "source /proj/plasma/CODE/<username>/infra/activate.sh" >> ~/.bashrc

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

## Incus AI sandbox

A single long-lived, unprivileged Debian trixie container for running coding
agents against your real working directories:

    scripts/setup/incus-ai-sandbox.sh      # provision once
    scripts/setup/incus-ai-profiles.sh     # optional local/cloud instances
    ln -s $PWD/scripts/ai-sandbox.sh ~/bin/ai
    cd ~/code/some-project
    ai                                     # ai-local when provisioned

`~/code` and `~/proj` are always attached at their real absolute paths, so all
code and project checkouts remain visible even when you start `ai` from inside
one of them. A directory outside those roots is attached at the same absolute
path for that session and detached again when the last session in it exits.
Several directories can be attached concurrently. The container reaches the
internet but not the LAN, the host, or any unattached host path, and starts with
no software installed beyond the stock image. Details, network policy, and the
boundary this does and does not provide: [`docs/ai-sandbox.md`](docs/ai-sandbox.md).

## Multipass AI coding VM

The Multipass profile creates the same headless Ubuntu LTS environment on ARM64
and x86-64 hosts. It defaults to 8 CPUs, 16 GB RAM, and a 128 GB disk:

    scripts/multipass-ai.sh

Override resources with flags or `MULTIPASS_AI_*` environment variables:

    scripts/multipass-ai.sh --name ai-work --cpus 12 --memory 24G

The launcher deliberately does not use the special instance name `primary` and
does not mount any host directory. Code starts in `~/workspace`; mount or copy
only explicitly approved paths later. The small
[`cloud-init/multipass-ai.yaml`](cloud-init/multipass-ai.yaml) file delegates to
the same `scripts/setup/apt.sh ai` installer that can be run directly on Debian
or Ubuntu, avoiding a second package definition.

Codex, Claude Code, OpenCode, Pi, uv, chezmoi, GitHub/GitLab CLIs, compilers,
scientific libraries, and the common CachyOS-derived CLI tools are installed.
Normal CLI logins and provider tokens persist inside the VM, but no credential
is baked into cloud-init or Git. To install/update private Helpy and Sloptools
after GitHub login:

    gh auth login
    ai-private-tools

That script installs the two stdio MCP servers and constrains Sloptools' project
root to `~/workspace`. Re-run `ai-update` for public AI CLIs and
`ai-private-tools` for the private MCP tools.

Personal settings stay separate from this generic public repository. After Git
authentication, an optional private chezmoi repository (including age-encrypted
files) can be applied and later updated with:

    ai-user-config git@github.com:YOUR-ORG/YOUR-DOTFILES.git
    ai-user-config

API keys that cannot use a CLI login may be kept in
`~/.config/ai-infra/env` with mode `0600`; an example is created alongside it.
This file remains local to the VM unless the user deliberately manages an
encrypted version with chezmoi.
