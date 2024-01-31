#!/bin/sh

REPO=$1

if [ -n "$GIT_HTTPS" ]; then
    URL=https://user:$READ_TOKEN@gitlab.tugraz.at/plasma/codes/$REPO
else
    URL=git@gitlab.tugraz.at/plasma/codes/$REPO
fi

git clone $URL
