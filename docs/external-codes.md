# External codes

Setup scripts for third-party codes live in `scripts/setup/`. Most install into
`$CODE/external` and provide an environment module in `modules/`.

## GPEC and MARS (Intel toolchain)

GPEC and MARS need the Intel toolchain with the classic `ifort` compiler.
Install the compiler together with HDF5 and NetCDF modules:

    scripts/setup/compiler_intel.sh

Then install the codes into `$CODE/external/intel`:

    scripts/setup/gpec.sh
    scripts/setup/mars.sh

Load them with

    module load gpec
    module load mars

Do not build GNU Fortran based codes in the same shell.

## OMFIT

OMFIT uses its own `conda` Python environment. Both are installed into
`$CODE/external` with

    deactivate
    scripts/setup/omfit.sh

Load and start OMFIT with

    deactivate
    source $CODE/external/mambaforge/bin/activate omfit
    module load omfit
    omfit

Do not use the standard Python virtual environment in the same shell.
