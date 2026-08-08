set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/sparse)

add_executable(test_sparse_mod.x test_sparse_mod.f90)
target_link_libraries(test_sparse_mod.x PRIVATE neo
                                                  sparse
                                                  util_for_test)
add_test(NAME test_sparse_mod COMMAND test_sparse_mod.x)

set_tests_properties(
    test_sparse_mod
    PROPERTIES LABELS "sparse")
