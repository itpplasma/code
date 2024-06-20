#!/bin/bash

apt-get install -y -q --no-install-recommends \
    texlive-latex-base \
    texlive-latex-recommended \
    texlive-publishers \
    texlive-science \
    texlive-bibtex-extra \
    texlive-luatex \
    latexmk \
    biber \
    ghostscript \
    lyx \
    doxygen-latex \
    poppler-utils
