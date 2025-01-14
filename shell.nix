with (import <nixpkgs> {});
mkShell {
  buildInputs = [
    # Base
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
    netcdf
    netcdffortran

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
