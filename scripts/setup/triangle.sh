#!/bin/bash

CC=${CC:-gcc}

if [ "$(uname)" == "Darwin" ]; then
    CFLAGS="-I/opt/homebrew/include"
    LDFLAGS="-L/opt/homebrew/lib"
fi


echo "Fetching and building Triangle..."
git clone https://salsa.debian.org/science-team/triangle.git
pushd triangle
    while read -r patch; do
        patch -p1 < "debian/patches/$patch"
    done < debian/patches/series

	$CC triangle.c -o triangle.o $CFLAGS -O2 -DTRILIBRARY -fPIC -DPIC -c
	$CC -shared triangle.o -o libtriangle-1.6.so $LDFLAGS -lm
	ln -s libtriangle-1.6.so libtriangle.so

popd
