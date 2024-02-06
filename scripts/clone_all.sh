#!/bin/bash

GITHUB_REPOS="libneo spline BOOZER_MAGFIE SIMPLE GORILLA GORILLA_APPLETS NEO-2 NEO-RT"
GITLAB_REPOS="MEPHIT kim efit_to_boozer"

for REPO in $GITHUB_REPOS; do
    scripts/clone_github.sh $REPO
done

for REPO in $GITLAB_REPOS; do
    scripts/clone_gitlab.sh $REPO
done

# Contributed libraries for NEO-RT
mkdir contrib
cd contrib
../scripts/clone_github.sh quadpack
../scripts/clone_github.sh vode
cd ..
