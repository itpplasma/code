set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/sparse)

# libneo's static sparse archive wraps UMFPACK/SuiteSparse but, because this
# standalone project links libneo's build-tree archives directly via
# link_directories rather than the exported LIBNEO::sparse target, the
# transitive UMFPACK/SuiteSparse dependencies are not pulled in automatically.
# Provide the complete dependency closure explicitly so the test links on
# hosts where libneo ships the optional sparse library.
find_package(BLAS REQUIRED)
find_package(LAPACK REQUIRED)
find_library(UMFPACK_LIBRARY umfpack)
find_library(AMD_LIBRARY amd)
find_library(COLAMD_LIBRARY colamd)
find_library(SUITESPARSE_CONFIG_LIBRARY suitesparseconfig)

add_executable(test_sparse_mod.x test_sparse_mod.f90)
target_link_libraries(test_sparse_mod.x PRIVATE neo
                                                  sparse
                                                  ${UMFPACK_LIBRARY}
                                                  ${AMD_LIBRARY}
                                                  ${COLAMD_LIBRARY}
                                                  ${SUITESPARSE_CONFIG_LIBRARY}
                                                  BLAS::BLAS
                                                  LAPACK::LAPACK
                                                  util_for_test)
add_test(NAME test_sparse_mod COMMAND test_sparse_mod.x)

set_tests_properties(
    test_sparse_mod
    PROPERTIES LABELS "sparse")
