#!/bin/bash

BRANCH_NAME="${BRANCH_NAME:-main}" # Default branch name is "main"

# Check if the branch exists upstream
if git fetch origin "$BRANCH_NAME" &>/dev/null; then
  echo "Branch $BRANCH_NAME exists upstream. Checking it out..."
  git checkout "$BRANCH_NAME"
else
  echo "Branch $BRANCH_NAME does not exist upstream. Checking out main branch..."
  git checkout main # Replace with your main branch name if it's different
fi
