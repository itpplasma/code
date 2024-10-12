set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/field)

add_executable(test_jorek_field_fluxpumping.x test_jorek_field_fluxpumping.f90)
target_link_libraries(test_jorek_field_fluxpumping.x PRIVATE neo)
add_test(NAME test_jorek_field_fluxpumping COMMAND test_jorek_field_fluxpumping.x)

set_tests_properties(
    test_jorek_field_fluxpumping
    PROPERTIES LABELS "poincare")