import pytest

import numpy as np

from libneo.mgrid import MgridFile

@pytest.fixture
def testfile_path(data_path):
    return data_path / "LHD/VMEC/makegrid_alternative/mgrid_lhd_nfp10.nc"


@pytest.fixture
def mgrid(testfile_path):
    return MgridFile.from_file(testfile_path)


def test_mgrid_read(testfile_path):
    f = MgridFile.from_file(testfile_path)
    print(f)


def test_mgrid_read_write(mgrid):
    mgrid.write("/tmp/mgrid_test.nc")


def test_mgrid_read_write_read(mgrid):
    mgrid.write("/tmp/mgrid_test.nc")
    g = MgridFile.from_file("/tmp/mgrid_test.nc")

    # Assert if all variables are the same
    assert mgrid.ir == g.ir
    assert mgrid.jz == g.jz
    assert mgrid.kp == g.kp
    assert mgrid.nfp == g.nfp
    assert mgrid.rmin == g.rmin
    assert mgrid.zmin == g.zmin
    assert mgrid.rmax == g.rmax
    assert mgrid.zmax == g.zmax
    assert mgrid.mgrid_mode == g.mgrid_mode
    assert np.array_equal(mgrid.coil_group, g.coil_group)
    assert np.array_equal(mgrid.coil_current, g.coil_current)
    assert np.array_equal(mgrid.br, g.br)
    assert np.array_equal(mgrid.bp, g.bp)
    assert np.array_equal(mgrid.bz, g.bz)


if __name__ == "__main__":
    pytest.main([__file__, "-s"])
