# CODE

Meta-repository of the ITPcp plasma group at TU Graz with scripts to set up,
build and use the group's internal and external codes. Debian is the standard
for development machines and agent VMs. Production servers and their Computor
development containers are exceptions. The recommended editor is Visual Studio Code.

## Contents

- Setup scripts for system packages and external codes (`scripts/setup/`)
- Shell activation with a Python virtual environment from `requirements.txt`
  (`activate.sh`, `activate.fish`)
- Shell helpers (`scripts/util.sh`) and the `heavy-run` wrapper for
  memory-limited builds and tests
- Environment modules for third-party software (`modules/`)
- Container images (`images/`) and a VS Code devcontainer (`.devcontainer/`)
- Integration tests for libneo, NEO-2 and NEO-RT (`tests/`)

## Workspace layout

This repository is the `infra` directory of a workspace. Activation exports
`$CODE` (the workspace root) and `$INFRA` (`$CODE/infra`):

    $CODE/
      infra/      this repository (activation, setup scripts, modules, .venv)
      external/   prebuilt third-party libraries shared by all codes
      libneo/     code checkout
      SIMPLE/     code checkout
      ...

Codes resolve their dependencies as `$CODE/<name>` and read prebuilt libraries
from `$CODE/external`.

## Getting started

Authenticate with GitHub and GitLab using their CLIs or an SSH key. Do not put
credentials into this repository or a cloud-init file. For EUROfusion/IMAS
access see [docs/eurofusion-access.md](docs/eurofusion-access.md).

Preparing the machine:

- ITPcp computers have all required packages installed.
- On your own Debian system run `sudo scripts/setup/apt.sh full`.
- On macOS follow the Debian VM deployment in
  [docs/agent-zones.md](docs/agent-zones.md); its acceptance gates are still pending.
  [scripts/setup/mac.sh](scripts/setup/mac.sh) provides optional host development tools.
- On Windows install Debian via WSL2 with
  [scripts/setup/windows.bat](scripts/setup/windows.bat), then follow the Linux
  instructions.

Clone into the `infra` directory of your workspace (at the institute
`/proj/plasma/CODE/<username>`), run the setup and activate:

    git clone git@github.com:itpplasma/code /proj/plasma/CODE/<username>/infra
    cd /proj/plasma/CODE/<username>
    infra/scripts/setup.sh
    source infra/activate.sh

The setup installs external dependencies and creates the virtual environment in
`infra/.venv`. To activate the environment in every shell:

    echo "source /proj/plasma/CODE/<username>/infra/activate.sh" >> ~/.bashrc

When opening the workspace in VS Code, dismiss the prompt to initialise the
devcontainer unless you want to use it.

## Testing

    pytest tests/
    heavy-run ctest -j 1 --output-on-failure

The Python tests need the `libneo` Python package and, for some cases, private
reference data. See [docs/testing.md](docs/testing.md).

## Documentation

- [docs/testing.md](docs/testing.md): integration tests, reference data, `heavy-run`
- [docs/external-codes.md](docs/external-codes.md): GPEC, MARS and OMFIT
- [docs/agent-zones.md](docs/agent-zones.md): Debian VM zones and deployment gates
- [docs/ai-sandbox.md](docs/ai-sandbox.md): Incus container for coding agents
- [docs/multipass-ai.md](docs/multipass-ai.md): Multipass VM for coding agents
- [docs/eurofusion-access.md](docs/eurofusion-access.md): EUROfusion/IMAS accounts
- [docs/development-model.md](docs/development-model.md): proposed build and
  release conventions (draft)

## Optional Git hygiene hook

`bash scripts/setup/git-hooks.sh` (add `--dry-run` to preview) installs the
group's global pre-commit hook from
[itpplasma/.github](https://github.com/itpplasma/.github). It checks commits for
secrets, accidentally added data or archives and size limits, then runs the
repository's own hooks. It needs Git, Python 3, curl and
[Gitleaks](https://github.com/gitleaks/gitleaks); if any is missing, it warns and
changes nothing. See the
[group contribution guidance](https://github.com/itpplasma/.github/blob/main/CONTRIBUTING.md).

## Retired repositories

[magdif](https://github.com/itpplasma/magdif) and
[NEO-EQ](https://github.com/itpplasma/NEO-EQ) are superseded by
[MEPHIT](https://github.com/itpplasma/MEPHIT); they and `magfie` are no longer in
`scripts/clone_all.sh` (see
[issue #17](https://github.com/itpplasma/code/issues/17)).
