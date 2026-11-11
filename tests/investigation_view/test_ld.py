"""LD: loading and validation of a report file (FR-VW-01..03, D6)."""
import copy
import hashlib
import json

import pytest

from physicore_collector.investigation_view import MAX_REPORT_BYTES
from physicore_collector.investigation_view.load import parse_report_bytes, read_report
from physicore_collector.localization.output import to_json

from .fx import EXAMPLE_JSON, example_dict, gen, load, to_bytes


def problems(data: bytes):
    r = parse_report_bytes(data)
    assert r.report is None
    return r.problems


def test_LD_01_the_shipped_example_loads():
    r = read_report(EXAMPLE_JSON)
    assert r.report is not None and not r.problems and not r.unreadable


def test_LD_02_digest_and_size_are_those_of_the_file_bytes():
    r = read_report(EXAMPLE_JSON)
    data = EXAMPLE_JSON.read_bytes()
    assert r.sha256 == hashlib.sha256(data).hexdigest() and r.size == len(data)


def test_LD_03_a_report_re_serialises_byte_identically():
    r = read_report(EXAMPLE_JSON)
    assert to_json(r.report) == EXAMPLE_JSON.read_text()


def test_LD_04_missing_file_is_unreadable_not_invalid(tmp_path):
    r = read_report(tmp_path / "nope.json")
    assert r.report is None and r.unreadable and r.problems


def test_LD_05_a_directory_is_unreadable(tmp_path):
    assert read_report(tmp_path).unreadable


def test_LD_06_invalid_utf8():
    assert any("UTF-8" in p for p in problems(b"\xff\xfe{}"))


def test_LD_07_byte_order_mark():
    assert any("byte order mark" in p for p in problems(b"\xef\xbb\xbf" + to_bytes(example_dict())))


@pytest.mark.parametrize("text", [b"", b"{", b"[1]", b"null", b'"x"', b"123", b'{"a":1}{"b":2}'])
def test_LD_08_not_a_json_object(text):
    assert problems(text)


def test_LD_09_duplicate_keys_are_rejected():
    raw = to_bytes(example_dict()).replace(b'"exit_code":0', b'"exit_code":0,"exit_code":0')
    assert any("duplicate key" in p for p in problems(raw))


@pytest.mark.parametrize("const", [b"NaN", b"Infinity", b"-Infinity"])
def test_LD_10_json_constants_are_rejected(const):
    raw = to_bytes(example_dict()).replace(b'"exit_code":0', b'"exit_code":' + const)
    assert any("not allowed" in p for p in problems(raw))


@pytest.mark.parametrize("num", [b"1.0", b"1e3", b"0.5", b"-2.5E-3"])
def test_LD_11_any_non_integer_number_is_rejected(num):
    raw = to_bytes(example_dict()).replace(b'"exit_code":0', b'"exit_code":' + num)
    assert any("non-integer" in p for p in problems(raw))


def test_LD_12_unsupported_schema_version():
    d = example_dict(); d["localization_schema_version"] = "2.0"
    assert any("unsupported" in p for p in problems(to_bytes(d)))


def test_LD_13_missing_schema_version():
    d = example_dict(); del d["localization_schema_version"]
    assert any("unsupported" in p for p in problems(to_bytes(d)))


def test_LD_14_unknown_top_level_key():
    d = example_dict(); d["extra"] = 1
    assert problems(to_bytes(d))


@pytest.mark.parametrize("path", [("symptoms", 0), ("links", 0), ("chains", 0), ("components", 0), ("lanes", 0), ("hypotheses", 0)])
def test_LD_15_unknown_key_in_every_record_type(path):
    d = example_dict(); d[path[0]][path[1]]["extra"] = 1
    assert problems(to_bytes(d))


def test_LD_16_unknown_key_in_nested_records():
    d = example_dict(); d["symptoms"][0]["onset"]["extra"] = 1
    assert problems(to_bytes(d))
    d = example_dict(); d["coverage"]["extra"] = 1
    assert problems(to_bytes(d))
    d = example_dict(); d["inputs"]["extra"] = 1
    assert problems(to_bytes(d))


def test_LD_17_too_large_by_one_byte(monkeypatch):
    import physicore_collector.investigation_view.load as L
    data = to_bytes(example_dict())
    monkeypatch.setattr(L, "MAX_REPORT_BYTES", len(data) - 1)
    assert any("larger" in p for p in L.parse_report_bytes(data).problems)
    monkeypatch.setattr(L, "MAX_REPORT_BYTES", len(data))
    assert L.parse_report_bytes(data).report is not None


def test_LD_18_limit_is_64_mib():
    assert MAX_REPORT_BYTES == 64 * 1024 * 1024


def test_LD_19_oversized_file_is_refused_before_reading(tmp_path, monkeypatch):
    import physicore_collector.investigation_view.load as L
    p = tmp_path / "big.json"; p.write_bytes(b" " * 50)
    monkeypatch.setattr(L, "MAX_REPORT_BYTES", 10)
    r = L.read_report(p)
    assert r.report is None and any("larger" in x for x in r.problems) and not r.unreadable


def test_LD_20_deeply_nested_json_is_a_problem_not_a_crash():
    assert problems(b"[" * 100000 + b"]" * 100000)
    assert problems(b'{"a":' * 5000 + b"1" + b"}" * 5000)


def test_LD_21_validation_problems_are_bounded():
    d = example_dict(); d["symptoms"] = [{"bad": 1}] * 100
    r = parse_report_bytes(to_bytes(d))
    assert r.report is None and len(r.problems) <= 21


def test_LD_22_exit3_report_with_problems_loads():
    d = example_dict(); d.update(exit_code=3, exit_reasons=["anomaly_config_invalid"], problems=[{"source": "anomaly_config", "path": "x", "message": "bad"}],
                                 lanes=[], symptoms=[], links=[], components=[], chains=[], hypotheses=[], blockers=[], caveats=[])
    assert load(d).report.exit_code == 3


def test_LD_23_generated_reports_load():
    for n, l in [(0, 1), (1, 1), (9, 2), (30, 5)]:
        load(gen(n, l))
