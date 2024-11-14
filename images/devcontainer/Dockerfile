# base image (fundamental installations)
FROM ghcr.io/itpplasma/devel

# Prevent user inputs
ENV DEBIAN_FRONTEND=noninteractive

# Locales
ENV LANG=en_US.UTF-8
ENV LANGUAGE=en_US:en
ENV LC_ALL=en_US.UTF-8

# Devcontainer
COPY scripts/setup/devcontainer.sh /tmp/setup_devcontainer.sh
RUN /tmp/setup_devcontainer.sh
