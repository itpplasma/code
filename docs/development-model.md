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
paths (`scripts`, `.venv`, `modules`) live under `$INFRA`. The codes resolve
their dependencies as `$CODE/<name>` and read prebuilt libraries from
`$CODE/external`. `infra` is no longer the workspace root, so its git tree stops
seeing the sibling checkouts.

## Dependency graph

Each code declares its first-party dependencies in CMake through `find_or_fetch`
(use `$CODE/<dep>` if present, else fetch from GitHub). The graph is therefore
self-describing: read it by inverting those declarations rather than maintaining
a list by hand.

Current hub: libneo. Consumers with a declared edge: SIMPLE, NEO-2, NEO-RT,
MEPHIT, KAMEL, rabe. Secondary first-party dependency: fortplot (SIMPLE,
NEO-RT). Single-consumer leaves: spline (NEO-RT), vode (NEO-RT), quadpack
(KAMEL). GORILLA and most small projects declare no first-party edge.

## Integration testing

The old umbrella CI built every code on each push to infra. It never passed (0
of 94 runs) because it built the unpinned tips of six repos in one sequential
job against a frozen release tarball. We retire it.

Replacement, in two layers:

1. Per-code CI at the source. Each code builds against the current libneo in its
   own CI, so a breaking change surfaces in the pull request that caused it.
2. Release-time reverse-dependency validation, owned by the upstream. Before
   libneo releases, it builds its tracked consumers against the candidate and
   gates the release on the result.

A tracked downstream is a code with a real `find_or_fetch` edge to the upstream
and its own CI/CD. Both are checkable, so the set is computed, not curated, and
the long tail of small projects drops out automatically.

## The one convention

Every tracked downstream honors `<DEP>_BRANCH` (environment or CMake cache
variable): when set, `find_or_fetch` fetches that dependency at the given ref
instead of the committed default. `LIBNEO_BRANCH` is the instance for libneo,
and SIMPLE already implements it. This single hook lets the upstream build any
downstream against any candidate ref with no commit to the downstream. It is the
multi-repo stand-in for a global build graph, and the only piece that has to be
standardized across the codes.

## Release model

Release branches, no release-candidate tags.

1. libneo cuts `release/YY.MINOR` off main.
2. For each tracked downstream, dispatch its integration workflow with
   `LIBNEO_BRANCH=release/YY.MINOR` (a `workflow_dispatch` input: no commit, no
   pull request). The downstream builds its own main against the candidate.
3. libneo polls the dispatched runs and gates the tag on all of them passing.
4. On all-green, tag `YY.MINOR.0` at the release-branch head.
5. A bot opens a bump pull request in each downstream pinning the tag, never the
   branch. The maintainer reviews and merges on their own schedule; no
   auto-merge.

Invariants:

- A downstream's main only ever pins released tags. Branch refs live only in the
  ephemeral dispatch override, never in a committed pin.
- Validation needs green CI, not merged pull requests, so waiting for
  maintainers never blocks the upstream release.
- No automatic downstream releases. A downstream releases on its own cadence.
  Propagate a release only along a real edge, and only when behavior changed
  (for example a libneo change that alters NEO-2 output, NEO-2 being itself an
  upstream of NEO-RT).

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
