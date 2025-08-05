! Pure Fortran functions for autodiff testing
module autodiff_functions
  implicit none

contains

  ! f(x) = x^2 + 2x + 1
  function f_fortran(x) result(y)
    real(8), intent(in) :: x
    real(8) :: y
    y = x * x + 2.0d0 * x + 1.0d0
  end function f_fortran

  ! g(x) = sin(x) * cos(x)  
  function g_fortran(x) result(y)
    real(8), intent(in) :: x
    real(8) :: y
    y = sin(x) * cos(x)
  end function g_fortran

  ! h(x) = exp(x) + log(x + 1)
  function h_fortran(x) result(y)
    real(8), intent(in) :: x
    real(8) :: y
    y = exp(x) + log(x + 1.0d0)
  end function h_fortran

  ! Multi-dimensional functions
  ! f2(x,y) = x^2 + y^2 + xy
  function f2_fortran(x, y) result(z)
    real(8), intent(in) :: x, y
    real(8) :: z
    z = x * x + y * y + x * y
  end function f2_fortran

  ! g2(x,y) = sin(x) * cos(y) + exp(x*y)
  function g2_fortran(x, y) result(z)
    real(8), intent(in) :: x, y
    real(8) :: z
    z = sin(x) * cos(y) + exp(x * y)
  end function g2_fortran

  ! h2(x,y) = log(x^2 + y^2 + 1) + x*y*sin(x+y)
  function h2_fortran(x, y) result(z)
    real(8), intent(in) :: x, y
    real(8) :: z
    z = log(x*x + y*y + 1.0d0) + x*y*sin(x + y)
  end function h2_fortran

end module autodiff_functions