# base image (fundamental installations)
FROM ghcr.io/itpplasma/base

# Prevent user inputs
ENV DEBIAN_FRONTEND=noninteractive

# Install interactive tools
COPY setup_interactive.sh /tmp/setup_interactive.sh
RUN apt-get update && bash /tmp/setup_interactive.sh && \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*

# Install development libraries
COPY setup_libs.sh /tmp/setup_libs.sh
RUN apt-get update && bash /tmp/setup_libs.sh && \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*

# Install Octave
COPY setup_octave.sh /tmp/setup_octave.sh
RUN apt-get update && bash /tmp/setup_octave.sh && \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*

# Install Python and dependencies
COPY setup_python.sh /tmp/setup_python.sh
RUN apt-get update && bash /tmp/setup_python.sh && \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*

# Prepare required packages for venv
COPY requirements.txt .
RUN python3 -m pip install -r requirements.txt --break-system-packages && \
	python3 -m pip cache purge
