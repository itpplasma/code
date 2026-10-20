# Development model (proposed)

Status: proposed, 2026-05. Open for group discussion before adoption.

Conventions for how the plasma codes and their shared dependencies are built and
released. This is a proposal from a design session, not settled policy.

## Workspace layout

`$CODE` is a plain workspace directory. It holds:

    $CODE/
      infra/      this repository: activation, setup scripts, modules, .venv
      external/   prebuilt third-party libraries, shared by all codes
      libneo/     code checkout
      SIMPLE/     code checkout
      ...

Activation exports `INFRA=$CODE/infra` and `CODE` as its parent. Infra-private
paths (`scripts`, `.venv`, `modules`) live under `$INFRA`. Codes read prebuilt
third-party libraries from `$CODE/external`. First-party dependencies are fetched
at a resolved ref, not read from the sibling checkouts (see Dependency graph), so
a local checkout is for editing that code, not for wiring one code against
another. `infra` is no longer the workspace root, so its git tree stops seeing
the sibling checkouts.

## Dependency graph

Each code declares its first-party dependencies in CMake through `find_or_fetch`,
which fetches each one from GitHub at a resolved ref (see The one convention).
There is no local-path shortcut: a sibling checkout is not consulted, so the
build is reproducible from the declaration alone. The graph is self-describing:
read it by inverting those declarations rather than keeping a list by hand.

Current hub: libneo. Consumers with a declared edge: SIMPLE, NEO-2, NEO-RT,
MEPHIT, KAMEL, rabe. Secondary first-party dependency: fortplot (SIMPLE,
NEO-RT). Single-consumer leaves: spline (NEO-RT), vode (NEO-RT), quadpack
(KAMEL). GORILLA and most small projects declare no first-party edge.

## Integration testing

The old umbrella CI built every code on each push to infra. It never passed (0
of 94 runs) because it built the unpinned tips of six repos in one sequential
job against a frozen release tarball. We retire it.

Replacement, in two layers:

1. Per-code CI at the source. Each code builds against the libneo release branch
   it tracks, so a breaking change surfaces in the pull request that caused it.
2. Release-time reverse-dependency validation, owned by the upstream. Before
   libneo releases, it dispatches each tracked consumer's own CI against the
   candidate and gates the release on the result.

A tracked downstream is a code with a real `find_or_fetch` edge to the upstream
and its own CI/CD. Both are checkable, so the set is computed, not curated, and
the long tail of small projects drops out automatically.

## The one convention

Every tracked downstream resolves each first-party dependency through one ladder
in `find_or_fetch`:

1. `<DEP>_REF` (environment or CMake cache): a branch, tag, or commit. It is
   validated against the remote and ignored if absent. The upstream sets it to
   build the downstream against a candidate.
2. `<DEP>_RELEASE` (committed cache variable): the release branch the code tracks
   by default. Never main.
3. the current branch if it exists in the remote, otherwise main.

`LIBNEO_REF` and `LIBNEO_RELEASE` are the instances for libneo. The override lets
the upstream build any downstream against any candidate ref with no commit to the
downstream. It is the multi-repo stand-in for a global build graph, and the only
piece standardized across the codes.

## Release model

Codes track the latest libneo release branch. Tags are reproducibility
snapshots, not what a code follows.

1. libneo cuts `release/YY.MINOR` off main.
2. For each tracked downstream, the release workflow dispatches the downstream's
   own CI with `LIBNEO_REF=release/YY.MINOR` (a `workflow_dispatch` input: no
   commit, no pull request). The downstream builds and tests its main against the
   candidate, golden records included.
3. libneo polls the dispatched runs and gates on all of them passing.
4. On all-green, a bot opens a bump pull request in each pinning downstream that
   sets `LIBNEO_RELEASE` to the new branch, and enables auto-merge. Each
   downstream's own CI gates that merge; no human step.
5. Tagging `YY.MINOR.PATCH` at the branch head is a separate, on-demand step. The
   tag is a citation and reproducibility snapshot; downstreams keep tracking the
   branch.

Properties:

- A downstream's committed pin is a release branch, never main. Exact reproduction
  uses a tag.
- Validation needs green CI, not merged pull requests, so a slow downstream never
  blocks the upstream.
- The gate dispatches each downstream's existing CI by name (`release/downstreams`
  lists the workflow file per repo). There is no separate integration workflow to
  maintain.
- Transitive consumers are listed explicitly. NEO-RT reaches libneo through NEO-2,
  so its CI sets `LIBNEO_REF` as an environment variable and it propagates to the
  libneo fetch inside the NEO-2 that NEO-RT builds.
- No automatic downstream releases. A downstream releases on its own cadence, and
  only when behavior changed (for example a libneo change that alters NEO-2
  output, NEO-2 being itself an upstream of NEO-RT).

Patch releases reuse the branch: cherry-pick onto `release/YY.MINOR`, re-run the
downstream dispatch, tag `YY.MINOR.PATCH`.

## Versioning

`YY.MINOR.PATCH`. `YY` is the two-digit year the release branch is cut, not the
year it finishes. MINOR counts releases within that year; PATCH counts fixes on
a release branch. Derive the version from the git tag (for example via
`setuptools_scm`) so the tag is the single source of truth, and feed CMake from
the same source.

No leading zeros in the version. PEP 440 normalizes `26.05` to `26.5`, so a
packaged library must use `26.5` or its metadata disagrees with the tag. Every
component is then a plain integer, which keeps git, CMake `project(VERSION)`,
Spack, and PEP 440 in agreement.

Teaching codes keep the semester scheme `YY.0M` (`26.03`, `26.10`). They are
end-user deliverables, not packaged libraries, so the leading-zero rule does not
reach them.

Rationale for CalVer over SemVer: the release process verifies downstream
compatibility by building, so the number need not carry SemVer's compatibility
promise. Its remaining job is to identify the dated snapshot, which is what
citation and reproducibility want. Precedent in scientific software includes
GROMACS (`2026.2`) and FEniCS (`2019.1.0`).
