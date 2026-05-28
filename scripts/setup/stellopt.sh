#!/usr/bin/env bash
set -e

# CODE_ROOT feeds the STELLOPT make configs, which read $(CODE_ROOT)/external/...
# That tree lives in the infra repo, so point it at $INFRA.
export CODE_ROOT="$INFRA"

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
            cp $INFRA/scripts/setup/stellopt/make_osx_brew_m1.inc $INFRA/external/STELLOPT/SHARE
            export MACHINE=osx_brew_m1
            ;;
        Linux)
            # Check for VSC5 cluster (hostname contains vsc or l5 node pattern)
            if [[ "$(hostname)" == *vsc* ]] || [[ "$(hostname)" == l5* ]] || [[ -n "$VSC_INSTITUTE" ]]; then
                cp $INFRA/scripts/setup/stellopt/make_vsc5.inc $INFRA/external/STELLOPT/SHARE
                export MACHINE=vsc5
            # Check for scluster
            elif [[ "$(hostname)" == scluster* ]]; then
                cp $INFRA/scripts/setup/stellopt/make_scluster.inc $INFRA/external/STELLOPT/SHARE
                export MACHINE=scluster
            # Detect distro family from /etc/os-release
            elif [ -f /etc/os-release ]; then
                . /etc/os-release
                case "$ID" in
                    arch|manjaro|cachyos|endeavouros|garuda|artix)
                        cp $INFRA/scripts/setup/stellopt/make_arch_linux.inc $INFRA/external/STELLOPT/SHARE
                        export MACHINE=arch_linux
                        ;;
                    rhel|centos|fedora|almalinux|rocky)
                        export MACHINE=redhat
                        ;;
                    debian|ubuntu|mint)
                        export MACHINE=ubuntu
                        ;;
                    *)
                        case "$ID_LIKE" in
                            *arch*)
                                cp $INFRA/scripts/setup/stellopt/make_arch_linux.inc $INFRA/external/STELLOPT/SHARE
                                export MACHINE=arch_linux
                                ;;
                            *rhel*|*centos*|*fedora*)
                                export MACHINE=redhat
                                ;;
                            *debian*|*ubuntu*)
                                export MACHINE=ubuntu
                                ;;
                            *)
                                # Fallback: check for pacman (Arch-based)
                                if command -v pacman &>/dev/null; then
                                    cp $INFRA/scripts/setup/stellopt/make_arch_linux.inc $INFRA/external/STELLOPT/SHARE
                                    export MACHINE=arch_linux
                                else
                                    export MACHINE=ubuntu
                                fi
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

apply_patches() {
    local patches_dir="$INFRA/scripts/setup/stellopt/patches"
    if [ -d "$patches_dir" ]; then
        for patch in "$patches_dir"/*.patch; do
            [ -f "$patch" ] || continue
            if git apply --check "$patch" 2>/dev/null; then
                echo "Applying patch: $(basename "$patch")"
                git apply "$patch"
            else
                echo "Patch already applied or not applicable: $(basename "$patch")"
            fi
        done
    fi
}

cd $INFRA/external

if [ ! -d "STELLOPT" ] ; then
    echo "Cloning STELLOPT..."
    git clone git@github.com:PrincetonUniversity/STELLOPT.git STELLOPT
fi

cd STELLOPT
export STELLOPT_PATH=$PWD

# Apply patches before building
apply_patches

select_machine
echo "Building STELLOPT with MACHINE=$MACHINE"
# Cap parallel jobs at 8 to avoid race conditions in STELLOPT's Makefile
nproc_safe=$(( $(nproc) > 8 ? 8 : $(nproc) ))
./build_all -o release -j ${NPROC:-$nproc_safe}
