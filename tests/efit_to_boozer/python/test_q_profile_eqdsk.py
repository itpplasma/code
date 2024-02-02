"""
Tests for efit_2_boozer and eqdsk_base
"""

import pytest

# Import standard modules
import numpy as np
import os

# Import modules to be tested
import efit2boozer

from numpy.testing import assert_allclose
from libneo import read_eqdsk, FluxLabelConverter


def test_q_profile_eqdsk():

    # Read data from EQDSK file
    basedir_eqdsk = "/proj/plasma/DATA/DEMO/teams/Equilibrium_DEMO2019_CHEASE"
    eqfile_eqdsk = "MOD_Qprof_Test/EQDSK_DEMO2019_q1_COCOS_02.OUT"

    eqdsk_data = read_eqdsk(os.path.join(basedir_eqdsk, eqfile_eqdsk))

    # The data in EQDSK is writting in poloidal flux label,
    # but efit2boozer uses toroidal flux label. Therefore, we need a mapping
    q_profile_eqdsk = eqdsk_data["qprof"]
    spol_eqdsk = np.linspace(0.0, 1.0, q_profile_eqdsk.shape[0])
    converter = FluxLabelConverter(q_profile_eqdsk)
    stor_eqdsk = converter.spol2stor(spol_eqdsk)

    # The safety factor can be calculated alternatively using field line integration
    # this is done as part of the symmetry flux transformation and given as output here

    q_profile_field_line_integration = []

    # inp_label = 1 makes it that efit2boozer.magdata_in_symfluxcoord_ext uses the
    # flux label si instead of the actual flux psi to determine which
    # flux surface one is on. Therefore psi is just set as a dummy variable here.
    inp_label = 1
    psi = np.array(0.0)
    theta = np.array(0.0)

    # current_directory = os.path.dirname(os.path.realpath(__file__))
    current_directory = os.getcwd()
    test_directory = os.path.join(current_directory, "tests/efit_to_boozer/python")
    os.chdir(test_directory)

    efit2boozer.efit_to_boozer.init()
    for si in stor_eqdsk:
        (q, _, _, _, _, _, _, _, _, _, _, _, _, _, _, _) = (
            efit2boozer.magdata_in_symfluxcoord_ext(inp_label, si, psi, theta)
        )
        q_profile_field_line_integration.append(q)

    q_profile_field_line_integration = np.array(q_profile_field_line_integration)

    os.chdir(current_directory)

    assert_allclose(q_profile_field_line_integration, q_profile_eqdsk, rtol=1e-2)
    print("Alternative safety factor calculation agrees with EQDSK file within 1%")
