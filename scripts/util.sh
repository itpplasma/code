#!/bin/sh

# Utility functions for handling codes.
# Usage: source util.sh
# Commands: run_in_dir, git_all, clone_gitlab, clone_github
#
# Author: Christopher Albert
# Date: 2024-03-01
# License: MIT

add_to_path() {
    if [ -d "$1" ] && [[ ":$PATH:" != *":$1:"* ]]; then
        PATH="$1${PATH:+":$PATH"}"
    fi
}

add_to_library_path() {
    if [ -d "$1" ] && [[ ":$LD_LIBRARY_PATH:" != *":$1:"* ]]; then
        LD_LIBRARY_PATH="$1${LD_LIBRARY_PATH:+":$LD_LIBRARY_PATH"}"
    fi
}


# Run a command in a directory.
# Usage: run_in_dir <dir> <command>
run_in_dir() (
    local dir=$1
    local cmd=$2
    pushd $dir > /dev/null
    $cmd
    popd > /dev/null
)

# Run a git command in all in the current directory and in top-level git subdirectories.
# Usage: git_all commit | push | pull | fetch | status | branch ...
git_all() (
    local cmd=$1
    git $cmd
    for dir in $(ls -d */); do
        if [ -d "$dir/.git" ]; then
            echo "$dir"
            run_in_dir $dir "git $cmd"
            echo ""
        fi
    done
)


# Clone a repository from https://gitlab.tugraz.at/plasma/codes/ .
# Usage: clone_gitlab <repo>
clone_gitlab() (
    local repo=$1

    if [ -n "$GIT_HTTPS" ]; then
        URL=https://user:$READ_TOKEN@gitlab.tugraz.at/plasma/codes/$repo
    else
        URL=git@gitlab.tugraz.at:plasma/codes/$repo.git
    fi

    git clone $URL
)


# Clone a repository from https://github.com/itpplasma/ .
clone_github() (
    local repo=$1

    if [ -n "$GIT_HTTPS" ]; then
    URL=https://oauth2:$GITHUB_TOKEN@github.com/itpplasma/$repo
    else
        URL=git@github.com:itpplasma/$repo.git
    fi

    git clone $URL
)
