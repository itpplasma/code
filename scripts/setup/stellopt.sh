#!/usr/bin/env bash
set -e

# Ensure CODE_ROOT is exported for make configs (can't use CODE - build_all uses it)
export CODE_ROOT="$CODE"

# Create ~/bin for libstell symlinks and ensure it's in PATH
mkdir -p ~/bin
if [[ ":$PATH:" != *":$HOME/bin:"* ]]; then
    export PATH="$HOME/bin:$PATH"
    # Add to .bashrc if not already there
    if ! grep -q 'export PATH="$HOME/bin:$PATH"' ~/.bashrc 2>/dev/null; then
        echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc
    fi
fi

select_machine() {
    case "$(uname -s)" in
        Darwin)
            cp $CODE/scripts/setup/stellopt/make_osx_brew_m1.inc $CODE/external/STELLOPT/SHARE
            export MACHINE=osx_brew_m1
            ;;
        Linux)
            # Check for VSC5 cluster (hostname contains vsc or l5 node pattern)
            if [[ "$(hostname)" == *vsc* ]] || [[ "$(hostname)" == l5* ]] || [[ -n "$VSC_INSTITUTE" ]]; then
                cp $CODE/scripts/setup/stellopt/make_vsc5.inc $CODE/external/STELLOPT/SHARE
                export MACHINE=vsc5
            # Check for scluster
            elif [[ "$(hostname)" == scluster* ]]; then
                cp $CODE/scripts/setup/stellopt/make_scluster.inc $CODE/external/STELLOPT/SHARE
                export MACHINE=scluster
            # Detect distro family from /etc/os-release
            elif [ -f /etc/os-release ]; then
                . /etc/os-release
                case "$ID" in
                    arch|manjaro)
                        cp $CODE/scripts/setup/stellopt/make_arch_linux.inc $CODE/external/STELLOPT/SHARE
                        export MACHINE=arch_linux
                        ;;
                    *)
                        case "$ID_LIKE" in
                            *rhel*|*centos*|*fedora*)
                                export MACHINE=redhat
                                ;;
                            *debian*|*ubuntu*)
                                export MACHINE=ubuntu
                                ;;
                            *arch*)
                                cp $CODE/scripts/setup/stellopt/make_arch_linux.inc $CODE/external/STELLOPT/SHARE
                                export MACHINE=arch_linux
                                ;;
                            *)
                                # Fallback: check ID directly
                                case "$ID" in
                                    rhel|centos|fedora|almalinux|rocky)
                                        export MACHINE=redhat
                                        ;;
                                    debian|ubuntu|mint)
                                        export MACHINE=ubuntu
                                        ;;
                                    *)
                                        export MACHINE=ubuntu
                                        ;;
                                esac
                                ;;
                        esac
                        ;;
                esac
            else
                export MACHINE=ubuntu
            fi
            ;;
        *)
            echo "Unknown OS: $(uname -s)" >&2
            export MACHINE=unknown
            ;;
    esac
}

cd $CODE/external

if [ ! -d "STELLOPT" ] ; then
    echo "Cloning STELLOPT..."
    git clone git@github.com:PrincetonUniversity/STELLOPT.git STELLOPT
fi

cd STELLOPT
export STELLOPT_PATH=$PWD
select_machine
echo "Building STELLOPT with MACHINE=$MACHINE"
./build_all -o release -j ${NPROC:-$(nproc)}
