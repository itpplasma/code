program test_jorek_field_fluxpumping
use, intrinsic :: iso_fortran_env, only: dp => real64
use neo_jorek_field, only: jorek_field_t

implicit none

call test_haowei_field

contains


subroutine test_haowei_field
    use neo_jorek_field, only: get_ranges_from_filename

    type(jorek_field_t) :: field
    character(len=200) :: fluxpumping_dir, filename
    character(len=400) :: path_to_fluxpumping_file

    real(dp) :: Rmin, Rmax, Zmin, Zmax, phimin, phimax

    fluxpumping_dir = "/proj/plasma/DATA/AUG/JOREK/2024-05_test_haowei_flux_pumping"
    filename = "exprs_Rmin1.140_Rmax2.130_Zmin-0.921_Zmax0.778_phimin0.000_phimax6.283_s40000.h5"
    path_to_fluxpumping_file = trim(adjustl(fluxpumping_dir)) // '/' // trim(filename)

    call field%jorek_field_init(path_to_fluxpumping_file)

    call get_ranges_from_filename(Rmin, Rmax, Zmin, Zmax, phimin, phimax, filename)
    call make_contour_plot(field, Rmin, Rmax, phimin, phimax, Zmin, Zmax)
end subroutine test_haowei_field

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