from decimal import Decimal

import pytest

from physicore_collector.anomaly import identity as I


@pytest.mark.parametrize("found,v,state", [(False, None, "absent"), (True, "", "empty"), (True, "nav", "known"), (True, None, "malformed"),
                                          (True, 7, "malformed"), (True, Decimal(7), "malformed"), (True, True, "malformed"), (True, ["a"], "malformed"),
                                          (True, " ", "known")])
def test_classify_text(found, v, state):
    assert I.classify_text(found, v) == state


def test_hardware_id_empty_is_known():
    assert I.classify_hardware_id(True, "") == "known"
    assert I.classify_hardware_id(False, None) == "absent"
    assert I.classify_hardware_id(True, 5) == "malformed"
    assert I.classify_hardware_id(True, None) == "malformed"


@pytest.mark.parametrize("v,state", [(bytes(16), "empty"), (bytes(range(16)), "known"), (bytes(15), "malformed"), (bytes(17), "malformed"),
                                    ([0] * 16, "empty"), (list(range(16)), "known"), ([0] * 15 + [256], "malformed"), ([0] * 15 + [True], "malformed"),
                                    ("0" * 16, "malformed"), (None, "malformed"), (7, "malformed"), ([Decimal(0)] * 16, "empty"), ([Decimal(1)] * 16, "known"),
                                    ([Decimal("1.5")] * 16, "malformed"), ([Decimal(0)] * 15 + [Decimal(300)], "malformed"), (["1"] * 16, "malformed")])
def test_classify_uuid(v, state):
    assert I.classify_uuid(True, v) == state


def test_uuid_absent():
    assert I.classify_uuid(False, None) == "absent"


def test_all_zero_with_one_nonzero_octet_is_known():
    assert I.classify_uuid(True, bytes(15) + b"\x01") == "known"


def test_render_raw():
    assert I.render_raw("absent", None, 64) == "<absent>"
    assert I.render_raw("empty", "", 64) == '""'
    assert I.render_raw("malformed", None, 64) == "NoneType: None"
    assert I.render_raw("malformed", Decimal(7), 64) == "number: 7"
    assert I.render_raw("empty", bytes(16), 200).startswith("all-zero bytes")
    assert len(I.render_raw("malformed", "x" * 500, 20).encode()) <= 20


def test_truncate_text():
    assert I.truncate_text("abc", 3) == ("abc", None)
    assert I.truncate_text("abcd", 3) == ("abc", 4)
    t, o = I.truncate_text("é" * 10, 5)      # never splits a character
    assert o == 20 and len(t.encode()) <= 5 and t == "éé"
    assert I.truncate_text("", 3) == ("", None)
