program test_poincare_fluxpumping
use, intrinsic :: iso_fortran_env, only: dp => real64
use neo_poincare, only: poincare_config_t
use util_for_test, only: print_test, print_fail, print_ok

implicit none

real(dp), parameter :: pi = 3.14159265358979_dp
character(len=*), parameter :: config_file = 'poincare.inp'
type(poincare_config_t) :: jorek_config

jorek_config%n_fieldlines = 20
jorek_config%fieldline_start_Rmin = 1.55_dp
jorek_config%fieldline_start_Rmax = 1.85_dp
jorek_config%fieldline_start_phi = 0.0_dp
jorek_config%fieldline_start_Z = 0.1_dp
jorek_config%n_periods = 1000
jorek_config%period_length = 2.0_dp * pi
jorek_config%integrate_err = 1.0e-8_dp
jorek_config%plot_Rmin = 1.0_dp
jorek_config%plot_Rmax = 2.25_dp
jorek_config%plot_Zmin = -1.0_dp
jorek_config%plot_Zmax = 1.0_dp

call make_poincare_fluxpumping

contains


subroutine make_poincare_fluxpumping
    use neo_poincare, only: make_poincare, read_config_file, poincare_config_t
    use neo_jorek_field, only: jorek_field_t

    character(len=512) :: jorek_file
    type(jorek_field_t) :: field
    type(poincare_config_t) :: config

    integer, parameter :: n_phi = 2
    real(dp), parameter :: Z_at_phi0 = 0.08_dp, dZ = 0.08_dp
    real(dp) :: phi(n_phi)
    integer :: idx_phi
    character(len=100) :: output_filename

    call print_test("make_poincare_fluxpumping")

    jorek_file ="/proj/plasma/DATA/AUG/JOREK/2024-05_test_haowei_flux_pumping/" // &
    "exprs_Rmin1.140_Rmax2.130_Zmin-0.921_Zmax0.778_phimin0.000_phimax6.283_s40000.h5"

    call field%jorek_field_init(jorek_file, spline_order=(/5,5,5/))

    call write_poincare_config(jorek_config)
    call read_config_file(config, config_file)
    call remove_poincare_config

    call linspace(0.0_dp, 2.0_dp * pi, n_phi, phi)
    do idx_phi = 1, n_phi - 1
        config%fieldline_start_phi = jorek_config%fieldline_start_phi + phi(idx_phi)
        config%fieldline_start_Z = Z_at_phi0 + dZ * sin(phi(idx_phi))
        write(output_filename, '(I0, A)') idx_phi, '.dat'
        call make_poincare(field, config, output_filename)
    end do

    call print_ok
end subroutine make_poincare_fluxpumping

subroutine write_poincare_config(config)
    use neo_poincare, only: poincare_config_t
    type(poincare_config_t), intent(in) :: config
    integer :: n_fieldlines
    real(dp) :: fieldline_start_Rmin, fieldline_start_Rmax
    real(dp) :: fieldline_start_phi, fieldline_start_Z
    integer :: n_periods
    real(dp) :: period_length
    real(dp) :: integrate_err
    real(dp) :: plot_Rmin, plot_Rmax
    real(dp) :: plot_Zmin, plot_Zmax
    integer :: file_id

    namelist /poincare/ &
                n_fieldlines, &
                fieldline_start_Rmin, &
                fieldline_start_Rmax, &
                fieldline_start_phi, &
                fieldline_start_Z, &
                n_periods, &
                period_length, &
                integrate_err, &
                plot_Rmin, &
                plot_Rmax, &
                plot_Zmin, &
                plot_Zmax

    n_fieldlines = config%n_fieldlines
    fieldline_start_Rmin = config%fieldline_start_Rmin
    fieldline_start_Rmax = config%fieldline_start_Rmax
    fieldline_start_phi = config%fieldline_start_phi
    fieldline_start_Z = config%fieldline_start_Z
    n_periods = config%n_periods
    period_length = config%period_length
    integrate_err = config%integrate_err
    plot_Rmin = config%plot_Rmin
    plot_Rmax = config%plot_Rmax
    plot_Zmin = config%plot_Zmin
    plot_Zmax = config%plot_Zmax

    open(newunit=file_id, file=config_file, status='unknown')
    write(file_id, nml=poincare)
    close(file_id)
end subroutine write_poincare_config

subroutine remove_poincare_config
    integer :: stat, file_id
    logical :: exists
    
    open(newunit=file_id, iostat=stat, file=config_file, status='old')
    if (stat == 0) close(file_id, status='delete')
    inquire(file=trim(config_file), exist=exists)
    if (exists) then
        call print_fail
        error stop
    end if
end subroutine remove_poincare_config

subroutine linspace(start, stop, n, x)
    real(dp), intent(in) :: start, stop
    integer, intent(in) :: n
    real(dp), intent(out) :: x(n)
    real(dp) :: dx
    integer :: i

    dx = (stop - start) / (n - 1)
    do i = 1, n
        x(i) = start + (i - 1) * dx
    end do
end subroutine linspace

end program test_poincare_fluxpumping