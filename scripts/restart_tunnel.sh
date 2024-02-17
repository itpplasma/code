#!/bin/bash

pkill -f "code"
nohup code tunnel --accept-server-license-terms --cli-data-dir /temp/`whoami`/.vscode/`hostname` > /dev/null &

