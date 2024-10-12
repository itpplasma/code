cmake_minimum_required(VERSION 3.10)
project(CodeTest Fortran)

enable_language(Fortran)
enable_testing()

set(LIBNEO_DIR $ENV{CODE}/libneo/build)

include_directories(${LIBNEO_DIR}/include)
link_directories(${LIBNEO_DIR})

add_subdirectory(field)
add_subdirectory(poincare)