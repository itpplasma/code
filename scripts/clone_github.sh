#!/bin/sh

REPO=$1

if [ -z "$GIT_HTTPS" ]; then
    URL=https://github.com/itpplasma/$REPO
else
    URL=git@github.com:itpplasma/$REPO
fi

git clone $URL
