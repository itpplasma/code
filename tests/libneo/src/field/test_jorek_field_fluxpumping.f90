program test_jorek_field_fluxpumping
use, intrinsic :: iso_fortran_env, only: dp => real64
use neo_jorek_field, only: jorek_field_t
use util_for_test, only: print_test, print_fail, print_ok
use util, only: get_random_numbers

implicit none
character(len=400) :: jorek_file

jorek_file = "/proj/plasma/DATA/AUG/JOREK/2024-05_test_haowei_flux_pumping/&
               &exprs_Rmin1.140_Rmax2.130_Zmin-0.921_Zmax0.778_phimin0.000_phimax6.283_s40000.h5"

call test_jorek_field
call draw_jorek_field_mesh
call draw_jorek_field

contains


subroutine test_jorek_field
    use neo_jorek_field, only: get_ranges_from_filename

    type(jorek_field_t) :: field

    real(dp) :: Rmin, Rmax, Zmin, Zmax, phimin, phimax

    call print_test("test_jorek_field")
   
    call field%jorek_field_init(jorek_file, spline_order=(/3,3,3/))

    call get_ranges_from_filename(Rmin, Rmax, Zmin, Zmax, phimin, phimax, jorek_file)
    Zmin = -0.4_dp ! only look in central region as near seperatrix unstable results
    Zmax = 0.4_dp
    Rmin = 1.5_dp
    Rmax = 1.85_dp
    call is_divb_0(field, Rmin, Rmax, phimin, phimax, Zmin, Zmax)
    call is_b_curla_plus_fluxfunction(field, Rmin, Rmax, phimin, phimax, Zmin, Zmax)

    call print_ok
end subroutine test_jorek_field

subroutine is_divb_0(field, Rmin, Rmax, phimin, phimax, Zmin, Zmax)
    use util_for_test_field, only: compute_cylindrical_divb

    type(jorek_field_t), intent(in) :: field
    real(dp), intent(in) :: Rmin, Rmax, phimin, phimax, Zmin, Zmax

    integer, parameter :: n = 1000
    real(dp), parameter :: tol = 1.0e-8_dp
    real(dp) :: divb, B(3), bnorm
    real(dp) :: x(3,n)
    integer :: idx, seed(8)

    seed = 12345678
    x(1,:) = get_random_numbers(Rmin, Rmax, n, seed=seed)
    x(2,:) = get_random_numbers(phimin, phimax, n, seed=seed)
    x(3,:) = get_random_numbers(Zmin, Zmax, n, seed=seed)

    do idx = 1, n
        divb = compute_cylindrical_divb(field, x(:, idx), tol/10000.0_dp)
        call field%compute_bfield(x(:, idx), B)
        bnorm = max(1.0_dp, sqrt(B(1)**2 + B(2)**2 + B(3)**2))
        if (abs(divb/bnorm) > tol) then
            print *, "at x = ", x(:, idx)
            print *, "div B = ", divb
            call print_fail
            error stop
        end if
    end do
end subroutine is_divb_0

subroutine is_b_curla_plus_fluxfunction(field, Rmin, Rmax, phimin, phimax, Zmin, Zmax)
    use util_for_test_field, only: compute_cylindrical_curla

    type(jorek_field_t), intent(in) :: field
    real(dp), intent(in) :: Rmin, Rmax, phimin, phimax, Zmin, Zmax

    integer, parameter :: n = 1000
    real(dp), parameter :: tol = 1.0e-7_dp
    real(dp) :: A(3), B(3), curla(3), fluxfunction, B_from_a_and_fluxfunction(3)
    real(dp) :: x(3,n), R
    integer :: idx

    x(1,:) = get_random_numbers(Rmin, Rmax, n)
    x(2,:) = get_random_numbers(phimin, phimax, n)
    x(3,:) = get_random_numbers(Zmin, Zmax, n)

    do idx = 1, n
        call field%compute_abfield(x(:,idx), A, B)
        call field%compute_fluxfunction(x(:,idx), fluxfunction)
        curla = compute_cylindrical_curla(field, x(:,idx), tol * 1e-5_dp)
        B_from_a_and_fluxfunction = curla
        R = x(1,idx)
        B_from_a_and_fluxfunction(2) = B_from_a_and_fluxfunction(2) + fluxfunction/R
        if (any(abs(B - B_from_a_and_fluxfunction) > tol)) then
            print *, "mis-match at x = ", x(:,idx)
            print *, "B = ", B
            print *, "B_from_a_and_fluxfunction = ", B_from_a_and_fluxfunction
            print *, "curla = ", curla
            print *, "fluxfunction/R = ", fluxfunction/R
            call print_fail
            error stop
        end if
    end do
end subroutine is_b_curla_plus_fluxfunction


subroutine draw_jorek_field_mesh
    use neo_jorek_field, only: load_field_mesh_from_jorek
    use neo_jorek_field, only: load_fluxfunction_mesh_from_jorek
    use neo_field_mesh, only: field_mesh_t
    use neo_mesh, only: mesh_t

    type(field_mesh_t) :: jorek_mesh, b_jorek_formula_mesh, b_cyl_formula_mesh
    type(mesh_t) :: A_phi_mesh, fluxfunction_mesh
    real(dp), dimension(:,:,:), allocatable :: R

    call print_test("draw_jorek_field_mesh")

    call load_field_mesh_from_jorek(jorek_file, jorek_mesh)
    call set_field_mesh_convention_to_jorek(jorek_mesh)
    call save_field_mesh_to_hdf5(jorek_mesh, 'jorek_field_mesh.h5')

    b_jorek_formula_mesh = jorek_mesh
    call set_b_mesh_to_jorek_formula(b_jorek_formula_mesh)
    call save_field_mesh_to_hdf5(b_jorek_formula_mesh, 'b_jorek_formula_mesh.h5')

    call print_ok
end subroutine draw_jorek_field_mesh

subroutine set_field_mesh_convention_to_jorek(field_mesh)
    use neo_field_mesh, only: field_mesh_t
    use neo_jorek_field, only: switch_phi_orientation

    type(field_mesh_t), intent(inout) :: field_mesh

    real(dp), dimension(:,:,:), allocatable :: R

    allocate(R(field_mesh%A2%n1, field_mesh%A2%n2, field_mesh%A2%n3))
    R = spread(spread(field_mesh%A2%x1, dim=2, ncopies=field_mesh%A2%n2), &
                                        dim=3, ncopies=field_mesh%A2%n3)
    call switch_phi_orientation(field_mesh%A1%value)
    call switch_phi_orientation(field_mesh%A2%value)
    field_mesh%A2%value = - field_mesh%A2%value * R
    call switch_phi_orientation(field_mesh%A3%value)
    call switch_phi_orientation(field_mesh%B1%value)
    call switch_phi_orientation(field_mesh%B2%value)
    field_mesh%B2%value = - field_mesh%B2%value
    call switch_phi_orientation(field_mesh%B3%value)
end subroutine set_field_mesh_convention_to_jorek

subroutine set_b_mesh_to_jorek_formula(field_mesh)
    use neo_field_mesh, only: field_mesh_t
    use neo_mesh, only: mesh_t

    type(field_mesh_t), intent(inout) :: field_mesh

    integer :: idx_R, idx_phi, idx_Z
    real(dp) :: R
    real(dp) :: dA3_dZ, dAZ_dphi, dAR_dphi, dA3_dR, dAZ_dR, dAR_dZ

    do idx_R = 2, field_mesh%A1%n1 - 1
        R = field_mesh%A1%x1(idx_R)
        do idx_phi = 2, field_mesh%A1%n2 - 1
            do idx_Z = 2, field_mesh%A1%n3 - 1
                    dAR_dphi = (field_mesh%A1%value(idx_R, idx_phi+1, idx_Z) - &
                                field_mesh%A1%value(idx_R, idx_phi-1, idx_Z)) / &
                               (field_mesh%A1%x2(idx_phi+1) - &
                                field_mesh%A1%x2(idx_phi-1))                                
                    dAR_dZ =   (field_mesh%A1%value(idx_R, idx_phi, idx_Z+1) - &
                                field_mesh%A1%value(idx_R, idx_phi, idx_Z-1)) / &
                               (field_mesh%A1%x3(idx_Z+1) - &
                                field_mesh%A1%x3(idx_Z-1))
                    dA3_dR =   (field_mesh%A2%value(idx_R+1, idx_phi, idx_Z) - &
                                field_mesh%A2%value(idx_R-1, idx_phi, idx_Z)) / &
                               (field_mesh%A2%x1(idx_R+1) - &
                                field_mesh%A2%x1(idx_R-1))
                    dA3_dZ =   (field_mesh%A2%value(idx_R, idx_phi, idx_Z+1) - &
                                field_mesh%A2%value(idx_R, idx_phi, idx_Z-1)) / &
                               (field_mesh%A2%x3(idx_Z+1) - &
                                field_mesh%A2%x3(idx_Z-1))
                    dAZ_dR =   (field_mesh%A3%value(idx_R+1, idx_phi, idx_Z) - &
                                field_mesh%A3%value(idx_R-1, idx_phi, idx_Z)) / &
                               (field_mesh%A3%x1(idx_R+1) - &
                                field_mesh%A3%x1(idx_R-1))
                    dAZ_dphi = (field_mesh%A3%value(idx_R, idx_phi+1, idx_Z) - &
                                field_mesh%A3%value(idx_R, idx_phi-1, idx_Z)) / &
                               (field_mesh%A3%x2(idx_phi+1) - &
                                field_mesh%A3%x2(idx_phi-1))
                    field_mesh%B1%value(idx_R, idx_phi, idx_Z) = (dA3_dZ - dAZ_dphi) / R
                    field_mesh%B2%value(idx_R, idx_phi, idx_Z) = (dAZ_dR - dAR_dZ)
                    field_mesh%B3%value(idx_R, idx_phi, idx_Z) = (dAR_dphi - dA3_dR) / R
            end do
        end do
    end do
end subroutine set_b_mesh_to_jorek_formula


subroutine draw_jorek_field
    use neo_jorek_field, only: get_ranges_from_filename

    type(jorek_field_t) :: field

    real(dp) :: Rmin, Rmax, Zmin, Zmax, phimin, phimax
   
    call print_test("draw_jorek_field")
    call field%jorek_field_init(jorek_file)
    call get_ranges_from_filename(Rmin, Rmax, Zmin, Zmax, phimin, phimax, jorek_file)
    call make_contour_plot(field, Rmin, Rmax, phimin, phimax, Zmin, Zmax)
    call print_ok
end subroutine draw_jorek_field

subroutine make_contour_plot(field, Rmin, Rmax, phimin, phimax, Zmin, Zmax)
    use neo_field_mesh, only: field_mesh_t

    type(jorek_field_t), intent(in) :: field
    real(dp), intent(in) :: Rmin, Rmax, phimin, phimax, Zmin, Zmax

    real(dp), dimension(3,2) :: limits
    integer :: n_points(3) = (/60, 60, 60/)
    type(field_mesh_t) :: field_mesh
    
    limits(:,1) = (/Rmin, phimin, Zmin/)
    limits(:,2) = (/Rmax, phimax, Zmax/)

    call field_mesh%field_mesh_init_with_field(limits, field, n_points)
    call save_field_mesh_to_hdf5(field_mesh, 'jorek_field.h5')
end subroutine make_contour_plot

subroutine save_field_mesh_to_hdf5(field_mesh, filename)
    use neo_field_mesh, only: field_mesh_t
    use hdf5_tools, only: hid_t, h5_init, h5_create, h5_add, h5_close, h5_deinit

    type(field_mesh_t), intent(in) :: field_mesh
    character(len=*) :: filename

    integer(hid_t) :: file_id
    integer :: n_R, n_phi, n_Z

    n_R = field_mesh%A1%n1
    n_phi = field_mesh%A1%n2
    n_Z = field_mesh%A1%n3
    call h5_init()
    call h5_create(filename, file_id)
    call h5_add(file_id, 'R', field_mesh%A1%x1, lbounds=(/1/), ubounds=(/n_R/))
    call h5_add(file_id, 'phi', field_mesh%A1%x2, lbounds=(/1/), ubounds=(/n_phi/))
    call h5_add(file_id, 'Z', field_mesh%A1%x3, lbounds=(/1/), ubounds=(/n_Z/)) 
    call h5_add(file_id, 'AR', field_mesh%A1%value, lbounds=(/1,1,1/), &
                                                    ubounds=(/n_R, n_phi, n_Z/))
    call h5_add(file_id, 'Aphi', field_mesh%A2%value, lbounds=(/1,1,1/), &
                                                        ubounds=(/n_R, n_phi, n_Z/))
    call h5_add(file_id, 'AZ', field_mesh%A3%value, lbounds=(/1,1,1/), &
                                                    ubounds=(/n_R, n_phi, n_Z/))
    call h5_add(file_id, 'BR', field_mesh%B1%value, lbounds=(/1,1,1/), &
                                                    ubounds=(/n_R, n_phi, n_Z/))
    call h5_add(file_id, 'Bphi', field_mesh%B2%value, lbounds=(/1,1,1/), &
                                                        ubounds=(/n_R, n_phi, n_Z/))
    call h5_add(file_id, 'BZ', field_mesh%B3%value, lbounds=(/1,1,1/), &
                                                    ubounds=(/n_R, n_phi, n_Z/))
    call h5_close(file_id)
    call h5_deinit()
end subroutine save_field_mesh_to_hdf5


end program test_jorek_field_fluxpumping