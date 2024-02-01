# base debian image
FROM debian:bookworm-slim

# Prevent user inputs
ENV DEBIAN_FRONTEND=noninteractive

# Base
RUN apt-get update && apt-get install -y -q --no-install-recommends \
		apt-transport-https \
		build-essential \
		ca-certificates \
		curl \
		libssl-dev \
		openssh-client \
		rsync \
		wget \
		git \
		gfortran \
		ninja-build \
		cmake \
		python3 \
		python3-dev \
		python3-pip \
		python3-venv \
		&& \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*

# entrypoint (bash)
CMD ["/bin/bash"]
