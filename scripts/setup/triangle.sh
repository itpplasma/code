#!/bin/bash

echo "Fetching and building Triangle..."
git clone https://salsa.debian.org/science-team/triangle.git
pushd triangle
    while read -r patch; do
        patch -p1 < "debian/patches/$patch"
    done < debian/patches/series

	$CC -I/opt/homebrew/include -O -DTRILIBRARY -fPIC -DPIC -c -o triangle.o triangle.c
	$CC  -L/opt/homebrew/lib -shared triangle.o -o libtriangle-1.6.so -lm
	ln -s libtriangle-1.6.so libtriangle.so

popd
