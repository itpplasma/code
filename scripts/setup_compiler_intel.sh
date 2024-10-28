#!/bin/bash
echo "Setting up Intel compiler..."

# download the key to system keyring
wget -O- https://apt.repos.intel.com/intel-gpg-keys/GPG-PUB-KEY-INTEL-SW-PRODUCTS.PUB \
    | sudo gpg --dearmor | sudo tee /usr/share/keyrings/oneapi-archive-keyring.gpg > /dev/null

# add signed entry to apt sources and configure the APT client to use Intel repository:
echo "deb [signed-by=/usr/share/keyrings/oneapi-archive-keyring.gpg] https://apt.repos.intel.com/oneapi all main" \
    | sudo tee /etc/apt/sources.list.d/oneAPI.list

# upgrade  & install
sudo apt-get update  --yes
sudo apt-get upgrade --yes
sudo apt-get install --yes --quiet --no-install-recommends intel-basekit intel-hpckit
sudo apt-get autoremove --yes

# create configuration for "environment modules"
if [ -x /opt/intel/oneapi/modulefiles-setup.sh ]
then
    if [ -d /usr/share/modules/modulefiles ]
    then
        /opt/intel/oneapi/modulefiles-setup.sh \
            --force --ignore-latest \
            --output-dir=/usr/share/modules/modulefiles/intel
        (cd /usr/share/modules/modulefiles/intel ; cleanlinks)
        for MOD_FILE in $(find -L /usr/share/modules/modulefiles/intel -type f -print);
        do # remove some of the "module-whatis" lines
            echo $MOD_FILE
            echo -e 'g/^module-whatis \"Version:/d\nw\nq' | ed $MOD_FILE
            echo -e 'g/^module-whatis \"Dependencies:/d\nw\nq' | ed $MOD_FILE
            echo -e 'g/^module-whatis \"URL:/d\nw\nq' | ed $MOD_FILE 
        done
    else
        echo "## ENVIRONMENT Modules - where is the config directory?"
    fi
else
    echo "## ENVIRONMENT Modules - don't know how to configure!"
fi
