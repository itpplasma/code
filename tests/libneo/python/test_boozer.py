import pytest

from pathlib import Path
import os
import numpy as np
import matplotlib.pyplot as plt

from libneo import BoozerFile
from libneo import read_eqdsk, FluxConverter

data_path = Path(os.environ["DATA"])

trial_boozer_file = data_path / "AUG/BOOZER/30835/out_neo-2_rmp_90-n0"
trial_eqdsk_file = data_path / "AUG/EQDSK/g30835.3200_ed6"
bc = BoozerFile(trial_boozer_file.as_posix())
eqdsk = read_eqdsk(trial_eqdsk_file.as_posix())
converter = FluxConverter(eqdsk["qprof"])
no_edges = slice(10, -10)


def test_iota():
    stor_bc = bc.s[no_edges]
    iota_bc = np.array(bc.iota[no_edges])
    stor_eqdsk = converter.sqrtspol2stor(np.sqrt(eqdsk["s_pol"]))
    iota_eqdsk = np.interp(stor_bc, stor_eqdsk, 1 / eqdsk["qprof"])
    assert np.allclose(
        iota_eqdsk, -iota_bc, atol=1e-2
    )  # .bc file has only positive itota


def test_get_rho_poloidal():
    sqrtspol = bc.get_rho_poloidal()[no_edges]
    sqrtspol_control = converter.stor2sqrtspol(bc.s)[no_edges]
    assert np.allclose(sqrtspol, sqrtspol_control, atol=1e-2)


if __name__ == "__main__":
    pytest.main([__file__, "-s"])
