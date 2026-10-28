# Testing

## Memory-limited runs with `heavy-run`

Activation adds `scripts/` to `PATH`, which provides the `heavy-run` wrapper for
workstation builds and tests with substantial memory use:

    heavy-run ctest -j 1 --output-on-failure
    HEAVY_RUN_MEM_HIGH=8G HEAVY_RUN_MEM_MAX=10G HEAVY_RUN_CPU=200% heavy-run make -j 2

The wrapper serialises jobs with the lock file `/run/user/$UID/heavy-tests.lock`
(override with `HEAVY_RUN_LOCK`) and runs each job in a user systemd scope with
`MemoryHigh=12G`, `MemoryMax=16G` and `MemorySwapMax=2G`. A CPU quota is
optional. OpenMP and OpenBLAS thread counts default to 1 when they are not
already set. `heavy-run --help` lists all overrides and the fallback lock path.

Behaviour without the required system support:

- Without user systemd, `heavy-run` warns and serialises with `flock` only.
- Without `flock`, it warns and runs without serialisation.
- Memory and CPU limits require user systemd with delegated controllers.

Before starting the payload, the wrapper checks that it runs in the expected
cgroup, that the memory controller is available and that the kernel's
`memory.max` matches the configured `MemoryMax`. If a check fails, the payload
is not run and the wrapper exits with status 125. `HEAVY_RUN_MEM_MAX` must be
finite.

Tests for the wrapper itself:

    bash tests/test_heavy_run.sh
    bash tests/test_heavy_run.sh --memory   # Linux with user systemd only

The `--memory` variant allocates 1 GiB in Python under `MemoryMax=512M`, touching
every page so that the allocation cannot be satisfied by the kernel zero page.
It reports RSS, `memory.current`, swap usage and `memory.events`, and repeats the
run with swap disabled; that run must end in an OOM kill with a `max` event.
The test scopes use `OOMPolicy=continue` so that the final event counters can
still be read.

## Python integration tests

    pytest tests/

The tests require the separate `libneo` Python package. The native
`_efit_to_boozer` extension is optional; its tests are skipped when it is not
available. Use `--code-path /path/to/workspace` (or set `$CODE`) to point to the
workspace containing `libneo`; the default is the parent directory of this
checkout.

### Reference data

Several tests use private reference equilibria from
`gitlab.tugraz.at/plasma/data`. Fetch selected subtrees with a read-only token:

    export GITLAB_ACCESS_TOKEN=<read-only-token>
    export DATA=$PWD/.testdata
    scripts/fetch_data.sh AUG/EQDSK AUG/BOOZER/30835 DEMO/EQDSK MASTU/EQDSK \
        LHD/VMEC/makegrid_alternative TESTS/libneo/eqdsk
    pytest tests/

The data location is taken from `--data-path`, then `$DATA`, then the
checkout's `.testdata` directory. Tests whose data are missing are skipped.

Golden records are read-only by default, and tests with missing records are
skipped. Use `--regenerate-golden` only after the new reference results have
been checked independently.

Notes on `scripts/fetch_data.sh`:

- Without a token it exits successfully without fetching anything.
- With a token, clone, checkout and LFS errors are fatal; install `git-lfs` for
  LFS-tracked data.
- In GitHub Actions it uses the data branch named like the current branch
  (`GITHUB_HEAD_REF`/`GITHUB_REF_NAME`), otherwise `main`, and falls back to
  `main` if that branch does not exist; `DATA_BRANCH` selects a branch
  explicitly.
- Only the requested subtrees are copied; repeated fetches update them in place.

The Python integration suite and the data fetch are run manually. The GitHub
workflows of this repository mirror it to GitLab and run the hygiene check; they
do not run these tests.
