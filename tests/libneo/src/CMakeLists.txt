cmake_minimum_required(VERSION 3.10)
project(CodeTest Fortran)

enable_language(Fortran)
enable_testing()

set(LIBNEO_DIR $ENV{CODE}/libneo/build)
set(LIBUTIL_FOR_TEST_DIR $ENV{CODE}/libneo/build/test/util_for_test/)
set(LIBUTIL_FOR_TEST_FIELD_DIR $ENV{CODE}/libneo/build/test/util_for_test_field/)

include_directories(${LIBNEO_DIR}/include)
link_directories(${LIBNEO_DIR})
link_directories(${LIBUTIL_FOR_TEST_DIR})
link_directories(${LIBUTIL_FOR_TEST_FIELD_DIR})

add_subdirectory(field)
add_subdirectory(poincare)

# Sparse front end integration test (itpplasma/code#13). Only build when the
# installed libneo ships the optional SuiteSparse-backed sparse library, so
# hosts without it are unaffected. This standalone project discovers libneo's
# build-tree libraries via link_directories rather than an imported package,
# so check for the sparse library itself instead of a CMake target.
find_library(LIBNEO_SPARSE_LIB sparse PATHS ${LIBNEO_DIR} NO_DEFAULT_PATH)
if(LIBNEO_SPARSE_LIB)
    add_subdirectory(sparse)
else()
    message(STATUS "libneo sparse library not found; skipping sparse integration test")
endif()
