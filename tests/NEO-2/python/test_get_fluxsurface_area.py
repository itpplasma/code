import pytest
import h5py
import numpy as np

@pytest.fixture
def run_files():
    from libneo import read_eqdsk
    filename_eqdsk = "/proj/plasma/DATA/AUG/EQUILIRBRIUM/QCE_discharge_Neon_shot_39561/eqdsk_39461_5.38s"
    eqdsk = read_eqdsk(filename_eqdsk)
    neo2_output = "/temp/grassl_g/Neon_discharge_axisymmetric_gleiter/RUN_lag8/neon_discharge.out"
    yield eqdsk, neo2_output

def test_get_fluxsurface_area_visual_check(run_files):
    from neo2_ql import get_fluxsurface_area
    from libneo import plot_fluxsurfaces, FluxConverter
    eqdsk, neo2_output = run_files

    area, stor = get_fluxsurface_area(neo2_output)
    converter = FluxConverter(eqdsk["qprof"])
    spol = converter.stor2spol(stor)

    plot_area_over_spol(spol, area)
    plot_fluxsurfaces(eqdsk, "fluxsurfaces")

def plot_area_over_spol(spol, area, show: bool=False):
    import matplotlib.pyplot as plt
    plt.figure()
    plt.plot(spol, area/1e4, "--r", label=r"fluxsurface area $S$")
    plt.xlabel(r"$s_\mathrm{pol}$")
    plt.ylabel(r"$S \mathrm{ [m^2]}$")
    plt.legend()
    plt.grid(True)
    if show:
        plt.show()

