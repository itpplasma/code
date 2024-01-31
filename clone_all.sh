#!/bin/sh

GITHUB_REPOS="libneo spline BOOZER_MAGFIE SIMPLE GORILLA NEO-2 NEO-RT"
GITLAB_REPOS="MEPHIT kim"

for REPO in $GITHUB_REPOS; do
    scripts/clone_github.sh $REPO
done

for REPO in $GITLAB_REPOS; do
    scripts/clone_gitlab.sh $REPO
done


mkdir contrib
cd contrib
clone_github.sh quadpack
clone_github.sh vode
cd ..
