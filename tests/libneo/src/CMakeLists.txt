cmake_minimum_required(VERSION 3.10)
project(CodeTest Fortran)

enable_language(Fortran)
enable_testing()

set(LIBNEO_DIR $ENV{CODE}/libneo/build)
set(LIBUTIL_FOR_TEST_DIR $ENV{CODE}/libneo/build/test/util_for_test/)

include_directories(${LIBNEO_DIR}/include)
link_directories(${LIBNEO_DIR})
link_directories(${LIBUTIL_FOR_TEST_DIR})

add_subdirectory(field)
add_subdirectory(poincare)