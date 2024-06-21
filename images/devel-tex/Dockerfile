# base image (fundamental installations)
FROM ghcr.io/itpplasma/devel

# Prevent user inputs
ENV DEBIAN_FRONTEND=noninteractive

# TexLive
COPY scripts/setup_texlive.sh /tmp/setup_texlive.sh
RUN apt-get update -y && /tmp/setup_texlive.sh && \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*

# TexLive Fonts
COPY scripts/setup_fonts.sh /tmp/setup_fonts.sh
RUN apt-get update -y && /tmp/setup_fonts.sh && \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*
