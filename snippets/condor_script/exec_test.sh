#!/bin/bash

echo -e "\nExecutable: $0   "
echo -e "Job number  : $1 \n"

file_content="$(cat test.in)"
echo "input read: ${file_content}" > test.out
