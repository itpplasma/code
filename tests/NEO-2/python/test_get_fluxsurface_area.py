import pytest

from libneo.eqdsk_base import approximate_fluxsurface_area_eqdsk


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
    import matplotlib.pyplot as plt

    eqdsk, neo2_output = run_files

    area, stor = get_fluxsurface_area(neo2_output)
    converter = FluxConverter(eqdsk["qprof"])
    spol = converter.stor2spol(stor)

    area_eqdsk, spol_eqdsk = approximate_fluxsurface_area_eqdsk(eqdsk)

    plt.figure()
    plt.plot(spol, area / 1e4, "-r", label=r"fluxsurface area $S$")
    plt.plot(spol_eqdsk, area_eqdsk, "--b", label=r"approx area from EQDSK")
    plt.xlabel(r"$s_\mathrm{pol}$")
    plt.ylabel(r"$S \; \mathrm{[m^2]}$")
    plt.legend()
    plt.grid(True)

    plot_fluxsurfaces(eqdsk, "fluxsurfaces")


if __name__ == "__main__":
    pytest.main()
