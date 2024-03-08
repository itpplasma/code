#!/bin/bash

GITHUB_REPOS="libneo spline BOOZER_MAGFIE SIMPLE GORILLA GORILLA_APPLETS NEO-2 NEO-RT"
GITLAB_REPOS="MEPHIT kim efit_to_boozer"

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
