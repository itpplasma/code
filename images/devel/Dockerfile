# base image (fundamental installations)
FROM ghcr.io/itpplasma/base

# Prevent user inputs
ENV DEBIAN_FRONTEND=noninteractive

# Set locales
ENV LANG=en_US.UTF-8
ENV LANGUAGE=en_US:en
ENV LC_ALL=en_US.UTF-8

# Install interactive tools
COPY scripts/setup/debian/interactive.sh /tmp/setup_interactive.sh
RUN apt-get update -y && bash /tmp/setup_interactive.sh && \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*

# Install development libraries
COPY scripts/setup/debian/libs.sh /tmp/setup_libs.sh
RUN apt-get update -y && bash /tmp/setup_libs.sh && \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*

# Install Octave
COPY scripts/setup/debian/octave.sh /tmp/setup_octave.sh
RUN apt-get update -y && bash /tmp/setup_octave.sh && \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*
