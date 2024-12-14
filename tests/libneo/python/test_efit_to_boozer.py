import pytest

import os
import tempfile
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt

import _efit_to_boozer as efit_to_boozer
from libneo.boozer import get_angles_and_transformation

efit_to_boozer_input = """3600      nstep    - number of integration steps
500       nlabel   - grid size over radial variable 
500       ntheta   - grid size over poloidal angle
10000     nsurfmax - number of starting points for separarix search
2000      nsurf    - number of flux surfaces in Boozer file
12        mpol     - number of poloidal modes in Boozer file
2119063900.d0 psimax - psi of plasma boundary
"""

field_divB0_input = """0                                 ipert        ! 0=eq only, 1=vac, 2,3=vac+plas
1                                 iequil       ! 0=pert. alone, 1=with equil.
1.00                              ampl         ! amplitude of perturbation, a.u.
72                                ntor         ! number of toroidal harmonics
0.99                              cutoff       ! inner cutoff in psi/psi_a units
4                                 icftype      ! type of coil file
'{gfile}'                         gfile        ! equilibrium file
'unused_field.dat'                pfile        ! coil        file
'unused_convexwall.dat'           convexfile   ! convex file for stretchcoords
'UNUSED_FLUXDATA'                 fluxdatapath ! directory with data in flux coord.
0                                 window size for filtering of psi array over R
0                                 window size for filtering of psi array over Z
"""

@pytest.fixture
def test_files(code_path, data_path):
    return {
        "CHEASE": data_path / "DEMO/EQDSK/Equilibrium_DEMO2019_CHEASE/MOD_Qprof_Test/EQDSK_DEMO2019_q1_COCOS_02.OUT",
        "AUG": data_path / "AUG/EQDSK/g30835.3200_ed6",
        "MASTU": data_path / "MASTU/EQDSK/MAST_47051_450ms.geqdsk",
        "local": code_path / "libneo/test/resources/input_efit_file.dat",
        "PROCESS": data_path / "DEMO/EQDSK/Equil_2021_PMI_QH_mode_betap_1d04_li_1d02_Ip_18d27MA_SOF.eqdsk",
        "standardized": data_path / "DEMO/EQDSK/Equil_2021_PMI_QH_mode_betap_1d04_li_1d02_Ip_18d27MA_SOF_std.eqdsk",
    }


def test_angles_and_transformation(test_files):
    stor = 0.4
    num_theta = 64
    for key, test_file in test_files.items():
        print(f"Testing {key} EQDSK file: {test_file}")
        tmp_path = init_run_path(test_file)
        print(f"Running in {tmp_path}")
        os.chdir(tmp_path)
        efit_to_boozer.efit_to_boozer.init()
        th_boozers, th_geoms, Gs = get_angles_and_transformation(stor, num_theta)

        plt.figure()
        plt.plot(th_boozers, th_geoms)


def init_run_path(gfile):
    tmp_path = Path(tempfile.mkdtemp())
    write_input_files(tmp_path, gfile)
    return tmp_path


def write_input_files(tmp_path, gfile):
    efit_to_boozer_input_file = tmp_path / "efit_to_boozer.inp"
    efit_to_boozer_input_file.write_text(efit_to_boozer_input)

    field_divB0_input_file = tmp_path / "field_divB0.inp"
    field_divB0_input_file.write_text(field_divB0_input.format(gfile=gfile))


if __name__ == "__main__":
    pytest.main([__file__, "-s"])
    plt.show()
