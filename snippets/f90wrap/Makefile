.PHONY: all clean

NAME=example
FILES_TO_WRAP=my.f90
F2PY_F2CMAP=.f2py_f2cmap
F90WRAP_OUTPUT=f90wrap_my.f90
F2PY_FORTRAN_OUTPUT=_$(NAME)-f2pywrappers.f
F2PY_C_OUTPUT=_$(NAME)module.c
OBJECT_FILES=my.o _$(NAME)-f2pywrappers.o _$(NAME)module.o

all: _$(NAME).so

$(F90WRAP_OUTPUT): $(FILES_TO_WRAP)
	f90wrap -m $(NAME) -k $(F2PY_F2CMAP) $(FILES_TO_WRAP)

$(F2PY_FORTRAN_OUTPUT): $(F90WRAP_OUTPUT)
	f2py-f90wrap -m _$(NAME) --f2cmap $(F2PY_F2CMAP) --lower $(F90WRAP_OUTPUT)

my.o: my.f90
	gfortran -fPIC -c my.f90

_$(NAME)-f2pywrappers.o: $(F2PY_FORTRAN_OUTPUT) $(F2PY_C_OUTPUT)
	gfortran -fPIC -c $(F2PY_FORTRAN_OUTPUT) $(F90WRAP_OUTPUT)
	gcc -fPIC -c $(F2PY_C_OUTPUT)

_$(NAME)module.o: _$(NAME)module.c
	gcc -fPIC -c _$(NAME)module.c

_$(NAME).so: $(OBJECT_FILES)
	gcc -shared -o _$(NAME).so $(OBJECT_FILES)

clean:
	rm -f $(OBJECT_FILES) $(F90WRAP_OUTPUT) $(F2PY_FORTRAN_OUTPUT) $(F2PY_C_OUTPUT) _$(NAME).so
