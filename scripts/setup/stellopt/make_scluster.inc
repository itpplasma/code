#######################################################################
#            scluster (Debian 12 with custom-built libs)
#######################################################################
#            Define Basic Utilities
#######################################################################
  SHELL = /bin/sh
  ARCH := $(shell uname -m)
  PWD1 = `pwd`
  MYHOME ?= $(HOME)/bin
  PRECOMP:= /lib/cpp -traditional -DLINUX
  COMPILE = $(FC)
  COMPILE_FREE = $(FC) -ffree-form -ffree-line-length-none -ffixed-line-length-none
  LINK    = ld $(FLAGS) -o
  LINK_AR = ar -ruv
  LINK_C  = $(CC) -shared -Wl,-z-defs

#######################################################################
#            Define Compiler Flags
#######################################################################
  FLAGS_R = -O2 -fexternal-blas -fbacktrace -march=native
  FLAGS_D = -g -O0 -fexternal-blas -fcheck=all
  # Use OpenBLAS (includes LAPACK)
  CODE_DIR := $(shell echo $$CODE)
  OPENBLAS_DIR = $(CODE_DIR)/external/OpenBLAS-0.3.28/install
  LIBS = -L$(OPENBLAS_DIR)/lib -lopenblas

  GFORTRAN_VERSION := $(shell $(FC) -dumpversion)
  GFORTRAN_MAJOR_VERSION := $(firstword $(subst ., ,$(GFORTRAN_VERSION)))
  ifeq ($(shell [ $(GFORTRAN_MAJOR_VERSION) -gt 9 ] && echo true),true)
      FLAGS_R += -fallow-argument-mismatch
      FLAGS_D += -fallow-argument-mismatch
  endif

#######################################################################
#            MPI Options
#######################################################################
  LMPI    = T
  MPI_COMPILE = mpif90.openmpi
  MPI_COMPILE_FREE = $(MPI_COMPILE) -ffree-form \
                     -ffree-line-length-none -ffixed-line-length-none
  MPI_COMPILE_C = mpicc.openmpi
  MPI_LINK = mpif90.openmpi
  MPI_RUN = mpiexec
  MPI_RUN_OPTS = --use-hwthread-cpus

#######################################################################
#            NAG Options
#######################################################################
  LNAG = F
  NAG_LIB =

#######################################################################
#            NETCDF Options
#######################################################################
  LNETCDF = T
  NETCDF_INC = $(shell nf-config --fflags)
  NETCDF_LIB = $(shell nf-config --flibs)

#######################################################################
#            NTCC Options
#######################################################################
  LNTCC = F
  NTCC_INC = -I$(NTCCHOME)/mod
  NTCC_LIB = -L$(NTCCHOME)/lib -laladdinsub -lr8slatec -ladpak\
             -lcppsub -lcomput -lpspline -lportlib -lezcdf -lmds_sub \
             -lmdstransp -lvaxonly

#######################################################################
#            HDF5 Options (from netcdf-install which includes HDF5)
#######################################################################
  LHDF5 = T
  HDF5_DIR = $(CODE_DIR)/external/netcdf-install
  HDF5_INC = -I$(HDF5_DIR)/include
  HDF5_LIB = -L$(HDF5_DIR)/lib -lhdf5_fortran -lhdf5_hl -lhdf5 -lz

#######################################################################
#             PGPLOT Options
#######################################################################
  LPGPLOT = F
  PGPLOT_INC =
  PGPLOT_LIB =

#######################################################################
#             SILO Options
#######################################################################
  LSILO = F
  SILO_INC =
  SILO_LIB =

#######################################################################
#            FFTW3 Options
#######################################################################
  LFFTW3 = F
  FFTW3_INC =
  FFTW3_LIB =

#######################################################################
#            DKES/NEO Options
#######################################################################
  LDKES = T
  LNEO  = T

#######################################################################
#            GENE Options
#######################################################################
ifneq ("$(wildcard $(GENE_PATH))","")
  LGENE = T
  GENE_INC = -I$(GENE_PATH)
  GENE_DIR = $(GENE_PATH)
  LIB_GENE = libgene.a
  GENE_LIB =
else
  LGENE = F
endif

#######################################################################
#            COILOPT++ Options
#######################################################################
ifneq ("$(wildcard $(COILOPT_PATH))","")
  LCOILOPT = T
  COILOPT_INC = -I$(COILOPT_PATH)
  COILOPTPP_DIR = $(COILOPT_PATH)
  LIB_COILOPTPP = libcoilopt++.a
  COILOPT_LIB = $(COILOPT_PATH)/$(LIB_COILOPTPP) \
                -L$(GSLHOME)/lib -lgsl -lgslcblas -lstdc++ -lmpi_cxx
else
  LCOILOPT = F
endif

#######################################################################
#            TERPSICHORE Options
#######################################################################
ifneq ("$(wildcard $(TERPSICHORE_PATH))","")
  LTERPSICHORE= T
  TERPSICHORE_INC = -I$(TERPSICHORE_PATH)
  TERPSICHORE_DIR = $(TERPSICHORE_PATH)
  LIB_TERPSICHORE = libterpsichore.a
  TERPSICHORE_LIB = $(TERPSICHORE_DIR)/$(LIB_TERPSICHORE)
else
  LTERPSICHORE = F
endif

#######################################################################
#            TRAVIS Options
#######################################################################
ifneq ("$(wildcard $(TRAVIS_PATH))","")
  LTRAVIS = T
  TRAVIS_DIR = $(TRAVIS_PATH)
  LIB_TRAVIS = libtravis64_sopt.a
  LIB_MCONF  = libmconf64.a
  LIB_FADDEEVA = libfaddeeva64.a
  TRAVIS_LIB = $(TRAVIS_DIR)lib/$(LIB_TRAVIS) \
               $(TRAVIS_DIR)faddeeva_package/lib/$(LIB_FADDEEVA) \
               $(TRAVIS_DIR)magconf/lib/$(LIB_MCONF) -lstdc++
else
  LTRAVIS = F
endif

#######################################################################
#            REGCOIL Options
#######################################################################
ifneq ("$(wildcard $(REGCOIL_PATH))","")
  LREGCOIL= T
  REGCOIL_DIR = $(REGCOIL_PATH)
  REGCOIL_INC = -I$(REGCOIL_DIR)
  LIB_REGCOIL = libregcoil.a
  REGCOIL_LIB = $(REGCOIL_DIR)/$(LIB_REGCOIL) -fopenmp
else
  LREGCOIL = F
endif

#######################################################################
#            SFINCS Options
#######################################################################
ifneq ("$(wildcard $(SFINCS_PATH))","")
  LSFINCS = T
  SFINCS_DIR = $(SFINCS_PATH)
  SFINCS_INC = -I$(SFINCS_DIR)
  LIB_SFINCS = libsfincs.a
  SFINCS_LIB = $(SFINCS_DIR)/$(LIB_SFINCS) \
             -L$(PETSC_DIR)/$(PETSC_ARCH)/lib -lpetsc -lX11
else
  LSFINCS = F
endif

#######################################################################
#            Available Energy Options
#######################################################################
ifneq ("$(wildcard $(AEOPT_PATH))","")
  LAEOPT = T
  AEOPT_DIR = $(AEOPT_PATH)
  AEOPT_INC = -I$(AEOPT_DIR)
  LIB_AEOPT = libtrapAE.a
  AEOPT_LIB = $(AEOPT_PATH)/$(LIB_AEOPT)
else
  LAEOPT = F
endif

#######################################################################
#            MANGO Options
#######################################################################
ifneq ("$(wildcard $(MANGO_PATH))","")
  LMANGO = T
  MANGO_DIR = $(MANGO_PATH)
  MANGO_INC =
  MANGO_LIB =
else
  LMANGO = F
endif

#######################################################################
#            LIBSTELL Shared Options
#######################################################################
LIB_SHARE = -lc -lgfortran -lstdc++ -lmpi -lmpi_mpifh -lz -lc -lm -lpthread $(LIBS) -lc
