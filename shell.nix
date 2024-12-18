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

    # Libs
    openblas
    suitesparse
    hdf5
    netcdf

    # Interactive
    coreutils
    which
    less
    vim
  ];
}
