# %%
from libneo import BoozerFile
from libneo import read_eqdsk, FluxConverter
import numpy as np
import matplotlib.pyplot as plt

trial_boozer_file = "/temp/AG-plasma/archive/2023/andfmar/Boozer_files_perturbation_field/ASDEX_U/30835_rmpm90/out_neo-2_rmp_m90-n0"
trial_eqdsk_file = "/proj/plasma/DATA/AUG/EQDSK/g30835.3200_ed6"
bc = BoozerFile(trial_boozer_file)
eqdsk = read_eqdsk(trial_eqdsk_file)
converter = FluxConverter(eqdsk["qprof"])
no_edges = slice(10,-10)

def test_iota():
    stor_bc = bc.s[no_edges]
    iota_bc = np.array(bc.iota[no_edges])
    stor_eqdsk = converter.sqrtspol2stor(np.sqrt(eqdsk["rho_poloidal"]))
    iota_eqdsk = np.interp(stor_bc, stor_eqdsk, 1/eqdsk["qprof"])
    assert np.allclose(iota_eqdsk, -iota_bc, atol=1e-2) # .bc file has only positive itota


def test_get_rho_poloidal():
    sqrtspol = bc.get_rho_poloidal()[no_edges]
    sqrtspol_control = converter.stor2sqrtspol(bc.s)[no_edges]
    assert np.allclose(sqrtspol, sqrtspol_control, atol=1e-2)

if __name__=="__main__":
    test_iota()
    test_get_rho_poloidal()