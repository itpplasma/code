program test_jorek_field_fluxpumping
use, intrinsic :: iso_fortran_env, only: dp => real64
use neo_jorek_field, only: jorek_field_t
use util_for_test, only: print_test, print_fail, print_ok

implicit none
character(len=200) :: haowei_dir, filename
character(len=400) :: haowei_file

haowei_dir = "/proj/plasma/DATA/AUG/JOREK/2024-05_test_haowei_flux_pumping"
filename = "exprs_Rmin1.140_Rmax2.130_Zmin-0.921_Zmax0.778_phimin0.000_phimax6.283_s40000.h5"
haowei_file = trim(adjustl(haowei_dir)) // '/' // trim(filename)

call test_haowei_field_mesh
call test_haowei_field
!call draw_haowei_field

contains


subroutine test_haowei_field_mesh
    use neo_jorek_field, only: load_field_mesh_from_jorek
    use neo_jorek_field, only: load_fluxfunction_mesh_from_jorek
    use neo_field_mesh, only: field_mesh_t
    use neo_mesh, only: mesh_t

    type(field_mesh_t) :: field_mesh
    type(mesh_t) :: fluxfunction_mesh, B_phi_mesh

    call print_test("test_haowei_field_mesh")

    call load_field_mesh_from_jorek(haowei_file, field_mesh)
    call load_fluxfunction_mesh_from_jorek(haowei_file, fluxfunction_mesh)
    call is_fluxfunction_mesh_equal_R_bphi(fluxfunction_mesh, field_mesh%B2)

    call print_ok
end subroutine test_haowei_field_mesh

subroutine is_fluxfunction_mesh_equal_R_bphi(fluxfunction, B_phi)
    use neo_mesh, only: mesh_t

    type(mesh_t), intent(in) :: fluxfunction, B_phi

    real(dp) :: R, delta_B_phi
    integer :: idx_R, idx_phi, idx_Z

    RADIUS: do idx_R = 1, fluxfunction%n1
        R = fluxfunction%x1(idx_R)
        do idx_phi = 1, 1
            do idx_Z = 1, fluxfunction%n3
                delta_B_phi = abs(fluxfunction%value(idx_R, idx_phi, idx_Z) & 
                                  - B_phi%value(idx_R, idx_phi, idx_Z) * R)
                if (delta_B_phi > 1.0e-10_dp) then
                    print *, "delta_B_phi = ", delta_B_phi
                    print *, "mis-match at knote = ", idx_R, idx_phi, idx_Z
                    print *, "fluxfunction = ", fluxfunction%value(idx_R, idx_phi, idx_Z)
                    print *, "B_phi * R = ", B_phi%value(idx_R, idx_phi, idx_Z) * R
                    call print_fail
                    cycle RADIUS
                end if
            end do
        end do
    end do RADIUS

end subroutine is_fluxfunction_mesh_equal_R_bphi


subroutine test_haowei_field
    use neo_jorek_field, only: get_ranges_from_filename

    type(jorek_field_t) :: field

    real(dp) :: Rmin, Rmax, Zmin, Zmax, phimin, phimax

    call print_test("test_haowei_field")
   
    call field%jorek_field_init(haowei_file)

    call get_ranges_from_filename(Rmin, Rmax, Zmin, Zmax, phimin, phimax, filename)
    call is_b_curla_plus_fluxfunction(field, Rmin, Rmax, phimin, phimax, Zmin, Zmax)

    call print_ok
end subroutine test_haowei_field

subroutine is_b_curla_plus_fluxfunction(field, Rmin, Rmax, phimin, phimax, Zmin, Zmax)
    use util_for_test_field, only: compute_cylindrical_curla

    type(jorek_field_t), intent(in) :: field
    real(dp), intent(in) :: Rmin, Rmax, phimin, phimax, Zmin, Zmax

    integer, parameter :: n = 1000
    real(dp), parameter :: tol = 1.0e-10_dp
    real(dp) :: A(3), B(3), curla(3), fluxfunction, B_from_a_and_fluxfunction(3)
    real(dp) :: x(3,n), R
    integer :: idx

    x(1,:) = get_random_numbers(Rmin, Rmax, n)
    x(2,:) = get_random_numbers(Zmin, Zmax, n)
    x(3,:) = get_random_numbers(phimin, phimax, n)

    do idx = 1, n
        call field%compute_abfield(x(:,idx), A, B)
        call field%compute_fluxfunction(x(:,idx), fluxfunction)
        curla = compute_cylindrical_curla(field, x(:,idx), tol)
        B_from_a_and_fluxfunction = curla
        R = x(1,idx)
        B_from_a_and_fluxfunction(2) = B_from_a_and_fluxfunction(2) + fluxfunction/R
        if (any(abs(B - B_from_a_and_fluxfunction) > tol)) then
            print *, "mis-match at x = ", x(:,idx)
            print *, "B = ", B
            print *, "B_from_a_and_fluxfunction = ", B_from_a_and_fluxfunction
            print *, "curla = ", curla
            print *, "fluxfunction = ", fluxfunction
            call print_fail
            error stop
        end if
    end do
end subroutine is_b_curla_plus_fluxfunction

function get_random_numbers(xmin, xmax, n, seed) result(x)
    real(dp), intent(in) :: xmin, xmax
    integer, intent(in) :: n
    integer, dimension(:), intent(in), optional :: seed
    real(dp), dimension(:), allocatable :: x

    if (present(seed)) then
        call random_seed(put=seed)
    end if
    allocate(x(n))
    call random_number(x)
    x = xmin + (xmax - xmin) * x
end function get_random_numbers


subroutine draw_haowei_field
    use neo_jorek_field, only: get_ranges_from_filename

    type(jorek_field_t) :: field

    real(dp) :: Rmin, Rmax, Zmin, Zmax, phimin, phimax
   
    call field%jorek_field_init(haowei_file)
    call get_ranges_from_filename(Rmin, Rmax, Zmin, Zmax, phimin, phimax, filename)
    call make_contour_plot(field, Rmin, Rmax, phimin, phimax, Zmin, Zmax)
end subroutine draw_haowei_field

subroutine make_contour_plot(field, Rmin, Rmax, phimin, phimax, Zmin, Zmax)
    use neo_field_mesh, only: field_mesh_t

    type(jorek_field_t), intent(in) :: field
    real(dp), intent(in) :: Rmin, Rmax, phimin, phimax, Zmin, Zmax

    real(dp), dimension(3,2) :: limits
    integer :: n_points(3) = (/200, 10, 200/)
    type(field_mesh_t) :: field_mesh
    
    limits(:,1) = (/Rmin, phimin, Zmin/)
    limits(:,2) = (/Rmax, phimax, Zmax/)

    call field_mesh%field_mesh_init_with_field(limits, field, n_points)
    call save_field_mesh_to_hdf5(field_mesh, 'jorek_contour.h5')

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