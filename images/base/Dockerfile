# base debian image
FROM debian:bookworm-slim

# Prevent user inputs
ENV DEBIAN_FRONTEND=noninteractive

# Enable non-free packages
RUN sed -i -e's/ main/ main contrib non-free non-free-firmware/g' \
		/etc/apt/sources.list.d/debian.sources

# Base
RUN apt-get update && apt-get install -y -q --no-install-recommends \
        sudo \
		environment-modules \
		unzip \
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

ARG USERNAME=plasma
ARG USER_UID=1000
ARG USER_GID=$USER_UID

# Create the user
RUN groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID -m $USERNAME \
    && echo $USERNAME ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/$USERNAME \
    && chmod 0440 /etc/sudoers.d/$USERNAME

# Add custom environment variables to .bashrc
RUN echo 'export PATH=$HOME/bin:$PATH' >> /home/$USERNAME/.bashrc && \
	echo 'export LD_LIBRARY_PATH=$HOME/bin:$LD_LIBRARY_PATH' >> \
		/home/$USERNAME/.bashrc && \
	echo 'export OMPI_ALLOW_RUN_AS_ROOT=1' >> /home/$USERNAME/.bashrc && \
	echo 'export OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1' >> /home/$USERNAME/.bashrc && \
	echo 'export USER=plasma' >> /home/$USERNAME/.bashrc && \
	echo 'source /usr/share/modules/init/sh' >> /home/$USERNAME/.bashrc

# Set the user
USER $USERNAME
WORKDIR /home/$USERNAME

# entrypoint (bash)
CMD ["/bin/bash"]
