# base image (fundamental installations)
FROM ghcr.io/itpplasma/devel

# Prevent user inputs
ENV DEBIAN_FRONTEND=noninteractive

# Locales
ENV LANG=en_US.UTF-8
ENV LANGUAGE=en_US:en
ENV LC_ALL=en_US.UTF-8

# TexLive
COPY scripts/setup/linux/setup_texlive.sh /tmp/setup_texlive.sh
RUN apt-get update -y && /tmp/setup_texlive.sh && \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*

# TexLive Fonts
COPY scripts/setup/linux/setup_fonts.sh /tmp/setup_fonts.sh
RUN apt-get update -y && /tmp/setup_fonts.sh && \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*
