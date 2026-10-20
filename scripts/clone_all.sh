#!/usr/bin/env bash

# GitHub repositories maintained and cloned by this meta-repository
# (see https://github.com/itpplasma). Retired/obsolete repositories are
# intentionally omitted; see README.md, "Retired repositories".
GITHUB_REPOS="libneo spline BOOZER_MAGFIE SIMPLE GORILLA GORILLA_APPLETS NEO-2 NEO-RT MEPHIT KAMEL"

# Obsolete repositories (archive work tracked separately in issue #17):
#   magdif  - obsolete, successor is itpplasma/MEPHIT (issue #17)
#   NEO-EQ  - obsolete, successor is itpplasma/MEPHIT (issue #17)
#   magfie  - no longer cloned; GitHub API returns 404 (2026-10-20)
# Keep these names out of GITHUB_REPOS above.

for REPO in $GITHUB_REPOS; do
    clone_github $REPO
done

for REPO in $GITLAB_REPOS; do
    clone_gitlab $REPO
done

# Contributed libraries for NEO-RT
mkdir contrib
cd contrib
clone_github quadpack
clone_github vode
cd ..
