import numpy as np

from libneo.mgrid import MgridFile

def test_mgrid_read():
    f = MgridFile.from_file('/proj/plasma/DATA/LHD/albert/vmec/makegrid_alternative/mgrid_lhd_nfp10.nc')
    print(f)

def test_mgrid_read_write():
    f = MgridFile.from_file('/proj/plasma/DATA/LHD/albert/vmec/makegrid_alternative/mgrid_lhd_nfp10.nc')
    f.write('/tmp/mgrid_test.nc')


def test_mgrid_read_write_read():
    f = MgridFile.from_file('/proj/plasma/DATA/LHD/albert/vmec/makegrid_alternative/mgrid_lhd_nfp10.nc')
    f.write('/tmp/mgrid_test.nc')
    g = MgridFile.from_file('/tmp/mgrid_test.nc')

    # Assert if all variables are the same
    assert f.ir == g.ir
    assert f.jz == g.jz
    assert f.kp == g.kp
    assert f.nfp == g.nfp
    assert f.rmin == g.rmin
    assert f.zmin == g.zmin
    assert f.rmax == g.rmax
    assert f.zmax == g.zmax
    assert f.mgrid_mode == g.mgrid_mode
    assert np.array_equal(f.coil_group, g.coil_group)
    assert np.array_equal(f.coil_current, g.coil_current)
    assert np.array_equal(f.br, g.br)
    assert np.array_equal(f.bp, g.bp)
    assert np.array_equal(f.bz, g.bz)
