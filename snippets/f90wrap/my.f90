module my_mod
    use iso_fortran_env, only: dp => real64
    implicit none

contains

    subroutine my_sub(a, b)
        real(dp), intent(in) :: a
        real(dp), intent(out) :: b

        b = a * 2.0_dp
    end subroutine my_sub

end module my_mod
