#/bin/bash

export GIT_HTTPS=1
source setup.sh
echo 'export GIT_HTTPS=1' >> /root/.bashrc
echo 'source /workspaces/code/activate.sh' >> /root/.bashrc
