"""
Tests for eqdsk.py
"""

import pytest

import json
import gzip

from libneo import eqdsk


@pytest.fixture
def test_files(code_path, data_path):
    return {
        "local": code_path / "libneo/test/resources/input_efit_file.dat",
        "PROCESS": data_path / "DEMO/EQDSK/Equil_2021_PMI_QH_mode_betap_1d04_li_1d02_Ip_18d27MA_SOF.eqdsk",
        "standardized": data_path / "DEMO/EQDSK/Equil_2021_PMI_QH_mode_betap_1d04_li_1d02_Ip_18d27MA_SOF_std.eqdsk",
        "AUG": data_path / "AUG/EQDSK/g30835.3200_ed6",
        # TODO: "MASTU": data_path / "MASTU/EQDSK/MAST_47051_450ms.geqdsk",
        # TODO: "CHEASE": data_path / "DEMO/teams/Equilibrium_DEMO2019_CHEASE/MOD_Qprof_Test/EQDSK_DEMO2019_q1_COCOS_02.OUT",
    }


def test_eqdsk_read(test_files):
    for key, test_file in test_files.items():
        print(f"Testing {key} EQDSK file: {test_file}")
        _ = eqdsk.eqdsk_file(test_file)


def test_eqdsk_golden_records(data_path, test_files):
    golden_record_path = data_path / "TESTS/libneo/eqdsk"
    store_golden_records(test_files.values(), golden_record_path)

    for test_file in test_files.values():
        eqdsk_object = eqdsk.eqdsk_file(test_file)

        data = eqdsk_object.__dict__
        replace_array_members_by_lists(data)

        assert are_dicts_equal(data, get_golden_record(test_file, golden_record_path))


def get_golden_record(file_path, storage_path):
    """
    Returns the golden record for the given test file.
    """

    file_base = file_path.stem
    with gzip.open(storage_path / f"{file_base}.json.gz", "rb") as f:
        compressed_data = f.read()
        json_data = compressed_data.decode("utf-8")
        return json.loads(json_data)


def store_golden_records(file_paths, storage_path, force_overwrite=False):
    """
    Stores reference data for regression tests. Call only manually after fixing
    a bug and checking that the new results are correct.
    """

    for f in file_paths:
        basedir = f.parent
        file_base = f.stem
        ext = f.suffix
        data = eqdsk.eqdsk_file(str(f)).__dict__
        replace_array_members_by_lists(data)

        outfile = storage_path / f"{file_base}.json.gz"

        if outfile.exists():
            print(f"Golden record {outfile} exists.")
            if not force_overwrite:
                continue

        with gzip.open(outfile, "wb") as f:
            json_data = json.dumps(data, indent=4)
            json_bytes = json_data.encode("utf-8")
            f.write(json_bytes)


def replace_array_members_by_lists(data):
    """
    Replaces numpy arrays by lists in a dictionary. This is needed because
    numpy arrays are not JSON serializable.
    """
    import numpy as np

    for key, value in data.items():
        if isinstance(value, dict):
            replace_array_members_by_lists(value)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, np.ndarray):
                    value[i] = item.tolist()
        elif isinstance(value, np.ndarray):
            data[key] = value.tolist()


def are_dicts_equal(dict1, dict2, tolerance=1e-9):
    """
    Compares two dictionaries. Returns True if they match, False otherwise.
    For float members, the comparison is done using math.isclose() with
    the given tolerance.
    """
    import math

    for key, value1 in dict1.items():
        if key not in dict2:
            return False
        value2 = dict2[key]
        if isinstance(value1, list) and isinstance(value2, list):
            if not are_float_lists_equal(value1, value2, tolerance):
                return False
        elif isinstance(value1, float) and isinstance(value2, float):
            if not math.isclose(value1, value2, rel_tol=tolerance):
                return False
        elif value1 != value2:
            return False
    return True


def are_float_lists_equal(list1, list2, tolerance=1e-9):
    """
    Compares two lists of floats. Returns True if they match, False otherwise.
    The comparison is done using math.isclose() with the given tolerance.
    """
    import math

    if len(list1) != len(list2):
        return False
    for a, b in zip(list1, list2):
        if isinstance(a, float) and isinstance(b, float):
            if not math.isclose(a, b, rel_tol=tolerance):
                return False
        elif isinstance(a, list) and isinstance(b, list):
            if not are_float_lists_equal(a, b, tolerance):
                return False
    return True


if __name__ == "__main__":
    pytest.main([__file__, "-s"])
