# base image (fundamental installations)
FROM ghcr.io/itpplasma/base

# Prevent user inputs
ENV DEBIAN_FRONTEND=noninteractive

# Interactive
RUN apt-get update && apt-get upgrade -y -q --no-install-recommends && \
	apt-get install -y -q --no-install-recommends \
		procps \
		nano \
		vim \
		htop \
		ncdu \
		less \
		gdb \
		cmake-curses-gui \
		&& \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*

# Install dev libraries
RUN apt-get update && apt-get install -y -q --no-install-recommends \
		pkg-config \
		libsuitesparse-dev \
		libmetis-dev \
		libtriangle-dev \
		libopenblas-dev \
		libsuperlu-dev \
		libhdf5-dev \
		libhdf5-openmpi-dev \
		libnetcdf-dev \
		libnetcdff-dev \
		libfftw3-dev \
		libgsl-dev \
		libopenmpi-dev \
		libscalapack-openmpi-dev \
		libpcre3-dev \
		libreadline-dev \
		h5utils \
		hdf5-tools \
		netcdf-bin \
		&& \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*

# Install Octave
RUN apt-get update && apt-get install -y -q --no-install-recommends \
		octave \
		&& \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*

# Install Python and dependencies
RUN apt-get update && apt-get install -y -q --no-install-recommends \
		python3-wheel python3-setuptools \
		python3-ipython jupyter python3-sphinx python3-matplotlib \
		python3-openpyxl python3-xlrd python3-h5py python3-netcdf4 \
		python3-sympy python3-uncertainties python3-numpy python3-scipy \
		python3-numba python3-pandas python3-sklearn python3-torch \
		python3-boto3 python3-lmfit python3-configobj python3-dill \
		python3-pyodbc python3-pyotp \
		fortran-language-server python3-tqdm python3-pytest \
		python3-mpi4py python3-skbuild idle3 \
		&& \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*

# Prepare required packages for venv
COPY requirements.txt .
RUN python3 -m pip install -r requirements.txt --break-system-packages && \
	python3 -m pip cache purge

# Install dependencies for fenicsx
RUN apt-get update && apt-get install -y -q --no-install-recommends \
		libboost-filesystem-dev \
		libboost-timer-dev \
		libpugixml-dev \
		petsc-dev \
		slepc-dev \
		libparmetis-dev \
		libscotch-dev \
		&& \
	apt-get clean && rm -rf /var/cache/apt/* && \
	rm -rf /var/lib/apt/lists/* && rm -rf /tmp/*
