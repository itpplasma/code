! Integration test for the consolidated sparse_mod front end.
!
! sparse_mod is the UMFPACK/SuiteSparse front end solving the linear systems
! of the NEO-2 ripple solver, the MEPHIT MDE / helical-current iteration and
! KAMEL's QL-Balance / KIM electrostatic Poisson solve.  Three byte-level
! forks of the module used to live in NEO-2, MEPHIT and KAMEL; the libneo
! "unique version" is the single implementation all codes link against.
!
! This test exercises the libneo sparse_mod API used by all three consumers:
!   (a) sparse_example(1) against the reference vector kept in KAMEL's
!       QL-Balance/src/test/test_sparse.f90
!   (b) the decoupled-DOF case (structurally empty row/column): formerly
!       uninitialised heap entries must now come out as exact zeros
!   (c) the complex overload
!   (d) sparse_solve_method 2 vs 3 (umf4solr vs umf4sol) both converge
!   (e) the iopt 1/2/3 factorize-solve-free cycle used by
!       KAMEL/QL-Balance/src/base/evolvestep.f90
!
! See itpplasma/code#13.

program test_sparse_mod
  use iso_fortran_env, only: dp => real64
  use sparse_mod

  implicit none

  ! (e) factorize-solve-free cycle, checked for both refinement settings and
  ! (d) sparse_solve_method 2 vs 3 both converge
  sparse_solve_method = 2
  call run_cycle
  sparse_solve_method = 3
  call run_cycle

  ! (a) reference example: A*x = 1 solves to
  !     {-1.17021, 0.234043, -0.0212766, -2.28723, 0.255319}
  call check_ref

  ! (b) decoupled DOF: an all-zero row/column must leave x == 0 exactly
  call check_decoupled_dof

  ! (c) complex overload
  call check_complex

  print *, "PASS: test_sparse_mod"

contains

  subroutine check_solution(A, x, b)
    real(dp), intent(in) :: A(:, :), x(:)
    real(dp), allocatable, intent(in) :: b(:)
    real(dp) :: max_abs_err, max_rel_err
    call sparse_solver_test(A, x, b, max_abs_err, max_rel_err)
    if (max_abs_err > 1.0e-8_dp) then
       print *, "FAIL: residual too large: max_abs_err=", max_abs_err
       error stop
    end if
  end subroutine check_solution

  ! factorize (iopt=2), solve reusing the factorisation (iopt=1),
  ! solve and free (iopt=3)
  subroutine run_cycle
    real(dp), allocatable :: A(:, :), x(:), b(:)
    call load_mini_example(A)
    allocate(x(size(A, 2)), b(size(A, 2)))
    x = 2.0_dp
    b = x
    call sparse_solve(A, x, 2)          ! factorize
    call check_solution(A, x, b)
    x = 3.0_dp
    b = x
    call sparse_solve(A, x, 1)          ! reuse factorisation
    call check_solution(A, x, b)
    x = 4.0_dp
    b = x
    call sparse_solve(A, x, 3)          ! solve and free
    call check_solution(A, x, b)
    deallocate(A, x, b)
  end subroutine run_cycle

  ! Reference residual from KAMEL/QL-Balance/src/test/test_sparse.f90
  subroutine check_ref
    real(dp), allocatable :: A(:, :), x(:), b(:)
    real(dp) :: ref(5)
    call load_mini_example(A)
    allocate(x(size(A, 2)), b(size(A, 2)))
    x = 1.0_dp
    b = x
    call sparse_solve(A, x)
    call check_solution(A, x, b)
    ref = [ -1.17021_dp, 0.234043_dp, -0.0212766_dp, -2.28723_dp, 0.255319_dp ]
    if (.not. all(abs(x - ref) < 1.0e-4_dp)) then
       print *, "FAIL: sparse_example(1) reference mismatch"
       print *, "got  ", x
       print *, "want ", ref
       error stop
    end if
    deallocate(A, x, b)
  end subroutine check_ref

  ! The decoupled DOF used to carry uninitialised heap because UMFPACK
  ! leaves empty/decoupled rows unwritten.  The consolidated copy zero-inits.
  ! Recipe from itpplasma/code#13: zero row and column 3 of the non-singular
  ! 5x5 load_mini_example matrix so DOF 3 is structurally decoupled; the
  ! decoupled entry of the solution must come out as an exact zero.
  subroutine check_decoupled_dof
    real(dp), allocatable :: M(:, :), xx(:), b(:)
    call load_mini_example(M)
    allocate(xx(size(M, 2)), b(size(M, 2)))
    M(:, 3) = 0.0_dp        ! structurally decouple DOF 3
    M(3, :) = 0.0_dp
    xx = 1.0_dp
    b = xx
    call sparse_solve(M, xx)
    if (abs(xx(3)) /= 0.0_dp) then
       print *, "FAIL: decoupled DOF not zero: ", xx(3)
       error stop
    end if
    ! full system (no decoupling) must still solve
    M = 0.0_dp
    call load_mini_example(M)
    xx = 1.0_dp
    b = xx
    call sparse_solve(M, xx)
    call check_solution(M, xx, b)
    deallocate(M, xx, b)
  end subroutine check_decoupled_dof

  subroutine check_complex
    complex(dp), allocatable :: Z(:, :), zx(:), zb(:)
    real(dp), allocatable :: M(:, :)
    real(dp) :: max_abs_err, max_rel_err
    integer :: i
    call load_mini_example(M)
    allocate(Z(size(M, 1), size(M, 2)), zx(size(M, 2)), zb(size(M, 2)))
    do i = 1, size(M, 2)
       Z(:, i) = cmplx(M(:, i), M(:, i), dp)
    end do
    zx = (1.0_dp, 1.0_dp)
    zb = zx
    call sparse_solve(Z, zx)
    call sparse_solver_test(Z, zx, zb, max_abs_err, max_rel_err)
    if (max_abs_err > 1.0e-8_dp) then
       print *, "FAIL: complex residual too large: max_abs_err=", max_abs_err
       error stop
    end if
    deallocate(Z, zx, zb, M)
  end subroutine check_complex

end program test_sparse_mod
