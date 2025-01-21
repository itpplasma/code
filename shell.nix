with (import <nixpkgs> {});
mkShell {
  buildInputs = [
    # Base
    stdenv
    cacert
    curl
    wget
    git
    openssh
    cmake
    ninja
    gcc14
    gfortran14
    mpi
    python313

    # Libs
    zlib
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
    export LD_LIBRARY_PATH=${pkgs.lib.makeLibraryPath [
      pkgs.stdenv.cc.cc
    ]}:${pkgs.zlib}/lib
  '';
}
