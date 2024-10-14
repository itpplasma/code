set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/poincare)

add_executable(test_poincare_fluxpumping.x test_poincare_fluxpumping.f90)
target_link_libraries(test_poincare_fluxpumping.x PRIVATE neo
                                                          util_for_test)
add_test(NAME test_poincare_fluxpumping COMMAND test_poincare_fluxpumping.x)

set_tests_properties(
    test_poincare_fluxpumping
    PROPERTIES LABELS "poincare")