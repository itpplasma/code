import pytest

import numpy as np
import matplotlib.pyplot as plt

from libneo import BoozerFile
from libneo import read_eqdsk, FluxConverter
from paths import data_path

no_edges = slice(10, -10)


@pytest.fixture
def boozer():
    trial_boozer_file = data_path / "AUG/BOOZER/30835/out_neo-2_rmp_90-n0"
    return BoozerFile(str(trial_boozer_file))


@pytest.fixture
def eqdsk():
    trial_eqdsk_file = data_path / "AUG/EQDSK/g30835.3200_ed6"
    return read_eqdsk(str(trial_eqdsk_file))


@pytest.fixture
def converter(eqdsk):
    return FluxConverter(eqdsk["qprof"])


def test_iota(boozer, eqdsk, converter):
    stor_bc = boozer.s[no_edges]
    iota_bc = np.array(boozer.iota[no_edges])
    stor_eqdsk = converter.sqrtspol2stor(np.sqrt(eqdsk["s_pol"]))
    iota_eqdsk = np.interp(stor_bc, stor_eqdsk, 1 / eqdsk["qprof"])
    assert np.allclose(
        iota_eqdsk, -iota_bc, atol=1e-2
    )  # .bc file has only positive itota


def test_get_rho_poloidal(boozer, converter):
    sqrtspol = boozer.get_rho_poloidal()[no_edges]
    sqrtspol_control = converter.stor2sqrtspol(boozer.s)[no_edges]
    assert np.allclose(sqrtspol, sqrtspol_control, atol=1e-2)


if __name__ == "__main__":
    pytest.main([__file__, "-s"])
