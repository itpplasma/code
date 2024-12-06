import pytest

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

    area_eqdsk, spol_eqdsk = get_fluxsurface_area_eqdsk(eqdsk)

    plt.figure()
    plt.plot(spol, area/1e4, "-r", label=r"fluxsurface area $S$")
    plt.plot(spol_eqdsk, area_eqdsk, "--b", label=r"approx area from EQDSK")
    plt.xlabel(r"$s_\mathrm{pol}$")
    plt.ylabel(r"$S \; \mathrm{[m^2]}$")
    plt.legend()
    plt.grid(True)

    plot_fluxsurfaces(eqdsk, "fluxsurfaces")


def get_fluxsurface_area_eqdsk(eqdsk, n_surf: int=11):
    import numpy as np
    import matplotlib.pyplot as plt

    normalized_flux = ((eqdsk["PsiVs"] - eqdsk["PsiaxisVs"]) / 
                            (eqdsk["PsiedgeVs"] - eqdsk["PsiaxisVs"]))
    spol = np.linspace(0, 1, n_surf)
    Zmin = np.min(eqdsk["Lcfs"][:,1])
    Zmax = np.max(eqdsk["Lcfs"][:,1])
    I = (eqdsk["Z"] > Zmin) * (eqdsk["Z"] < Zmax)
    fluxcontour = plt.contour(eqdsk["R"], eqdsk["Z"][I], normalized_flux[I], levels=spol)

    def compute_distance(p1, p2):
        return np.sqrt((p2[0] - p1[0])**2 + (p2[1] - p1[1])**2)

    circumference = []
    for flux_contour in fluxcontour.allsegs:
        total_length = 0
        points = np.array(flux_contour[0])
        for idx in range(1, len(points)):
            total_length += compute_distance(points[idx-1], points[idx])
        circumference.append(total_length)
    plt.close()
    
    area = 2*np.pi*eqdsk["R0"]*np.array(circumference)
    area = area[:-1] # exclude separatrix
    spol = spol[:-1]
    return area, spol


if __name__=="__main__":
    pytest.main()