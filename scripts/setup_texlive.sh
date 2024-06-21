#!/bin/bash

apt-get install -y -q --no-install-recommends \
    texlive-latex-base \
    texlive-latex-recommended

apt-get install -y -q --no-install-recommends \
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

if [ -n "$(ls /tmp/fmt* 2>/dev/null)" ]; then
  cat /tmp/fmt*
fi
