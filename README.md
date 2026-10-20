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

For EUROfusion/IMAS work, your account is requested by the group lead
(not self-service); see [docs/eurofusion-access.md](docs/eurofusion-access.md)
for the request route, SSH setup, and access verification.

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

A `v*` tag records a source snapshot with GitHub's automatic source archives.
Releases are created separately. Fresh setups run `infra/scripts/setup.sh`;
the devcontainer image in `.devcontainer/` provides a prepared environment.

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

Use `heavy-run` for workstation builds or tests with substantial memory use:

    heavy-run ctest -j 1 --output-on-failure
    HEAVY_RUN_MEM_HIGH=8G HEAVY_RUN_MEM_MAX=10G HEAVY_RUN_CPU=200% heavy-run make -j 2

Activation adds `scripts/` to `PATH`. The wrapper serializes jobs using
`/run/user/$UID/heavy-tests.lock` (override with `HEAVY_RUN_LOCK`) and runs them
in a user systemd scope with `MemoryHigh=12G`, `MemoryMax=16G`, and
`MemorySwapMax=2G`. CPU quota is optional. OpenMP/OpenBLAS thread counts default
to 1 only when unset. `heavy-run --help` lists overrides and the portable lock
path fallback. Without user systemd it warns and uses flock alone; without
flock it warns and omits serialization. Memory/CPU limits require user systemd
and delegated controllers. Before executing the payload in its named scope,
the wrapper checks cgroup membership, memory controller availability, and that
the kernel's finite `memory.max` matches the unit's configured `MemoryMax`.
A failed check refuses the payload (exit 125); `HEAVY_RUN_MEM_MAX` must be finite.

Run the wrapper checks with `bash tests/test_heavy_run.sh`. On a Linux host
with user systemd, `bash tests/test_heavy_run.sh --memory` also checks that a
1 GiB Python allocation hits `MemoryMax=512M`. It writes a nonzero byte to every
page: zero-filled allocations can survive through kernel zero-page reclamation.
The test verifies the payload's cgroup and limits, reports RSS, `memory.current`,
swap usage and `memory.events`, and always repeats with swap disabled. That run
must produce an OOM kill and a `max` event or the test fails. Disposable test
scopes use `OOMPolicy=continue` to retain an observer for final event readings.

Integration tests are run by

    pytest tests/

This will perform all the tests in `tests/` and its subfolders.

The Python tests require the separate `libneo` Python package; the native
`_efit_to_boozer` extension is optional and its tests skip when it is unavailable.
Use `--code-path /path/to/workspace` (or `$CODE`) for the workspace containing
`libneo`. The default is the parent of this infra checkout.

Several tests use private reference equilibria from
`gitlab.tugraz.at/plasma/data`. To fetch selected subtrees with a read-only token:

    export GITLAB_ACCESS_TOKEN=<read-only-token>
    export DATA=$PWD/.testdata
    scripts/fetch_data.sh AUG/EQDSK AUG/BOOZER/30835 DEMO/EQDSK MASTU/EQDSK \
        LHD/VMEC/makegrid_alternative TESTS/libneo/eqdsk
    pytest tests/

`--data-path` overrides `$DATA`, then the checkout's `.testdata` is used.
Without data, or when a required file is missing, the affected test skips.
Golden records are read-only by default; missing records also skip. Use
`--regenerate-golden` only after independently checking new reference results.

Without a token, the fetch script exits successfully without fetching. With a
token, clone, checkout and LFS errors fail the fetch; install `git-lfs` for LFS
data. The script tries the current GitHub branch, falling back to `main` if it
is absent; `DATA_BRANCH` selects a branch explicitly. Only requested subtrees
are copied, and repeated fetches update them without nesting directories.
The Python integration suite and DATA fetch are run manually; the current
GitHub workflow mirrors the repository and does not run these tests.



## Retired repositories

The following repositories are obsolete and intentionally omitted from
`scripts/clone_all.sh`. [Issue #17](https://github.com/itpplasma/code/issues/17)
tracks the separate history-preservation and archive work:

- [magdif](https://github.com/itpplasma/magdif) and
  [NEO-EQ](https://github.com/itpplasma/NEO-EQ): superseded by
  [MEPHIT](https://github.com/itpplasma/MEPHIT); neither was archived when
  checked on 2026-10-20.
- `magfie`: omitted from the maintained clone list. The GitHub repository API
  returned 404 on 2026-10-20; this does not distinguish deletion from lack of
  visibility.


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
