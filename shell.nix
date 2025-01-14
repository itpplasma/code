with (import <nixpkgs> {});
mkShell {
  buildInputs = [
    # Base
    curl
    wget
    git
    openssh
    cmake
    ninja
    gcc
    gfortran
    mpi

    # Libs
    openblas
    suitesparse
    hdf5
    hdf5-fortran
    netcdf
    netcdffortran
    fftw

    # Interactive
    coreutils
    which
    less
    vim
    lazygit
  ];

  shellHook = ''
    export CC=gcc
    export CXX=g++
    export FC=gfortran
  '';
}
