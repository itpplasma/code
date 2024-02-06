#!/bin/bash

REPO=$1

if [ -n "$GIT_HTTPS" ]; then
    URL=https://oauth2:$GITHUB_TOKEN@github.com/itpplasma/$REPO
else
    URL=git@github.com:itpplasma/$REPO.git
fi

git clone $URL
