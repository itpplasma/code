#!/usr/bin/env bash

pushd $CODE/external

if [ ! -d "STELLOPT" ] ; then
    echo "Fetching and building STELLOPT..."
    git clone --filter=blob:none https://github.com/PrincetonUniversity/STELLOPT.git STELLOPT
fi
    pushd STELLOPT
        export STELLOPT_PATH=$PWD
        export MYHOME=STELLOPT_PATH/build
        select_machine
        bash build_all
        find . -name "*.o" | xargs rm
    popd
popd

select_machine() {
    case "$(uname -s)" in
        Darwin)
            cp $CODE/scripts/setup/stellopt/make_osx_brew_m1.inc $CODE/external/STELLOPT/SHARE
            export MACHINE=osx_brew_m1
            ;;
        Linux)
            export MACHINE=ubuntu
            ;;
        *)
            echo "Unknown OS: $(uname -s)" >&2
            export MACHINE=unknown
            ;;
    esac
}
