# base debian image
FROM debian:bookworm-slim

# Prevent user inputs
ENV DEBIAN_FRONTEND=noninteractive

# Enable non-free packages
RUN sed -i -e's/ main/ main contrib non-free non-free-firmware/g' \
		/etc/apt/sources.list.d/debian.sources

# Update and upgrade
RUN apt-get update -y && apt-get upgrade -y -q --no-install-recommends && \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*

# Base
COPY scripts/setup/debian/base.sh /tmp/setup_base.sh
RUN apt-get update -y && bash /tmp/setup_base.sh && \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*

# Add custom environment variables to .bashrc
RUN echo 'export PATH=$HOME/bin:$PATH' >> /root/.bashrc && \
	echo 'export LD_LIBRARY_PATH=$HOME/bin:$LD_LIBRARY_PATH' >> \
		/root/.bashrc && \
	echo 'export OMPI_ALLOW_RUN_AS_ROOT=1' >> /root/.bashrc && \
	echo 'export OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1' >> /root/.bashrc && \
	echo 'export USER=root' >> /root/.bashrc && \
	echo 'source /usr/share/modules/init/sh' >> /root/.bashrc

# Locales
RUN sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && locale-gen
ENV LANG=en_US.UTF-8
ENV LANGUAGE=en_US:en
ENV LC_ALL=en_US.UTF-8

# entrypoint (bash)
CMD ["/bin/bash"]
