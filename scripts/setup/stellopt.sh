#!/usr/bin/env bash

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
            # Detect distro family from /etc/os-release
            elif [ -f /etc/os-release ]; then
                . /etc/os-release
                case "$ID_LIKE" in
                    *rhel*|*centos*|*fedora*)
                        export MACHINE=redhat
                        ;;
                    *debian*|*ubuntu*)
                        export MACHINE=ubuntu
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

pushd $CODE/external

if [ ! -d "STELLOPT" ] ; then
    echo "Fetching and building STELLOPT..."
    git clone git@github.com:itpplasma/STELLOPT.git STELLOPT
    pushd STELLOPT
        git remote add upstream git@github.com:PrincetonUniversity/STELLOPT.git
    popd
fi
    pushd STELLOPT
        export STELLOPT_PATH=$PWD
        select_machine
        bash build_all -o release -j $(nproc)
    popd
popd
