"""TA-DIAG-* (TEST_PLAN 3)."""
import pytest

from physicore_collector.anomaly.detectors.tier_a_diagnostics import DiagnosticsDetector
from physicore_collector.anomaly.findings import find_forbidden
from physicore_collector.investigator.cdr import decoder_info
from tests import investigator_fixtures as F
from tests.anomaly import fixtures as X
from tests.anomaly.detector_support import cfg, result_of, run_detectors

ARR = "diagnostic_msgs/msg/DiagnosticArray"
ST = "diagnostic_msgs/msg/DiagnosticStatus"
ID = "tierA.diagnostics"
C = cfg({ID: {"enabled": True}})
needs_decoder = pytest.mark.skipif(not decoder_info().available, reason="decoder extra not installed")


def el(level, name="motor", hw="hw1", message="m", values=None, **kw):
    d = {"level": level, "message": message, "values": values or []}
    if name is not ...:
        d["name"] = name
    if hw is not ...:
        d["hardware_id"] = hw
    d.update(kw)
    return d


def arr(*els, stamp=None):
    d = {"status": list(els)}
    if stamp:
        d["header"] = {"stamp": stamp, "frame_id": ""}
    return d


def stream(payloads, start=F.BASE, step=F.MS):
    return [(start + i * step, p) for i, p in enumerate(payloads)]


PAD = stream([arr(el(0, "pad", "p", f"ok {i}")) for i in range(40)])
PAD2 = stream([arr(el(0, "pad", "p", f"ok {i}")) for i in range(40)], start=F.BASE + 200 * F.MS)


def go(tmp_path, segments, *, typ=ARR, config=C, alternate=None, topic="/diagnostics"):
    bag = X.write_typed_bag(tmp_path / "bag", [{topic: s} for s in segments], {topic: typ}, alternate=alternate)
    out, ev, inv = run_detectors(bag, [DiagnosticsDetector()], config)
    return out, result_of(out, ID), out.findings


def test_01_levels_in_one_message(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(el(0, "a"), el(1, "b"), el(2, "c"), el(3, "d"))])])
    got = sorted((f.observed_value["level_name"], f.severity, f.severity_basis) for f in fs)
    assert got == [("ERROR", "error", "source_declared_level"), ("STALE", "warning", "source_declared_level"), ("WARN", "warning", "source_declared_level")]
    assert all(f.field_path_template == "status[*].level" for f in fs)


def test_02_undefined_levels(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(el(4), el(255))])])
    assert fs == [] and res.unrecognised_values == {"255": 1, "4": 1} and res.status == "insufficient_evidence"


def test_03_two_runs(tmp_path):
    ps = [arr(el(1))] * 50 + [arr(el(0))] + [arr(el(1))] * 7
    out, res, fs = go(tmp_path, [stream(ps)])
    assert sorted(f.occurrence_count for f in fs) == [7, 50]


@pytest.mark.parametrize("k,expect", [(1, 2), (2, 2), (3, 1)])
def test_04_absence_break_rule_boundary(tmp_path, k, expect):
    # WARN, absent for exactly 2 messages, WARN: breaks only when 2 >= K
    ps = [arr(el(1)), arr(el(0, "other", "o")), arr(el(0, "other", "o")), arr(el(1))]
    out, res, fs = go(tmp_path, [stream(ps)], config=cfg({ID: {"enabled": True, "absent_elements_break_run_after": k}}))
    assert len(fs) == expect


def test_04_without_k_absence_does_not_break(tmp_path):
    ps = [arr(el(1)), arr(), arr(), arr(el(1))]
    out, res, fs = go(tmp_path, [stream(ps)])
    assert len(fs) == 1 and fs[0].occurrence_count == 2


def test_05_message_changes_within_run(tmp_path):
    ps = [arr(el(1, message="a")), arr(el(1, message="b")), arr(el(1, message="a")), arr(el(1, message="c"))]
    out, res, fs = go(tmp_path, [stream(ps)])
    (f,) = fs
    assert f.occurrence_count == 4 and f.observed_value["distinct_messages"] == ["a", "b", "c"] and f.observed_value["distinct_messages_count"] == 3
    assert f.observed_value["message_first"] == "a" and f.observed_value["message_last"] == "c"


def test_06_19_14_empty_name_never_merged(tmp_path):
    out, res, fs = go(tmp_path / "a", [stream([arr(el(1, ""))] * 5)])
    assert len(fs) == 5 and all(f.occurrence_count == 1 for f in fs)
    assert all(any(n.code == "identity_unknown" for n in f.uncertainty) for f in fs)
    out, res, fs = go(tmp_path / "b", [stream([arr(el(3, ""))] * 3)])
    assert len(fs) == 3
    out, res, fs = go(tmp_path / "c", [stream([arr(el(2, ""), el(2, ""))])])
    assert len(fs) == 2 and sorted(f.observations[0].field_path for f in fs) == ["status[0].level", "status[1].level"]


def test_07_hardware_id_distinguishes(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(el(1, "m", "h1"), el(1, "m", "h2"))] * 3)])
    assert sorted(f.occurrence_count for f in fs) == [3, 3]


def test_08_values_first_last_inert_and_capped(tmp_path):
    v1 = [{"key": "k1", "value": "v1"}, {"key": "k2", "value": "x" * 200}]
    v2 = [{"key": f"k{i}", "value": str(i)} for i in range(9)]
    out, res, fs = go(tmp_path, [stream([arr(el(1, values=v1)), arr(el(1, values=v2))])])
    (f,) = fs
    assert f.observed_value["values_first"][0] == ["k1", "v1"] and len(f.observed_value["values_first"][1][1].encode()) <= 64
    assert len(f.observed_value["values_last"]) == 5            # max_retained_observations
    assert any(n.code == "text_truncated" for n in f.uncertainty)


def test_09_malformed_evaluation_fields(tmp_path):
    ps = [{"status": "x"}, {"nostatus": 1}, arr("notanelement"), arr(el("2")), arr(el(2.0)), arr(el(True)), arr(el(None))]
    out, res, fs = go(tmp_path, [stream(ps)])
    assert fs == [] and res.payload_malformed_field == 7 and res.status == "insufficient_evidence"


def test_17_absent_and_malformed_name_still_findings(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(el(1, ...), el(1, 7))])])
    assert len(fs) == 2 and sorted(f.observations[0].identity_state for f in fs) == ["absent", "malformed"]
    assert res.identity_field_malformed == {"name": 2}


def test_18_malformed_hardware_id_is_per_occurrence(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(el(2, "m", 5))] * 2)])
    assert len(fs) == 2 and res.identity_field_malformed == {"hardware_id": 2}
    assert all(f.observations[0].identity_state == "malformed" for f in fs)


def test_absent_hardware_id_is_unknown(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(el(2, "m", ...))] * 2)])
    assert len(fs) == 2 and res.identity_field_malformed == {"hardware_id": 2}


def test_10_empty_status_complete(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr()] * 3)])
    assert res.status == "no_anomaly_observed"


def test_10b_incomplete_recording(tmp_path):
    from physicore_collector.anomaly.runner import run, topic_schema_names, wanted_topics
    from physicore_collector.inventory import inspect_evidence
    from physicore_collector.investigator.observations import load_evidence
    bag = X.write_typed_bag(tmp_path / "bag", [{"/d": stream([arr()])}, {"/d": stream([arr()], start=F.BASE + 10**6)}], {"/d": ARR})
    inv = inspect_evidence(bag)
    (bag / "bag_1.mcap").write_bytes((bag / "bag_1.mcap").read_bytes() + b"y")
    d = DiagnosticsDetector()
    ev = load_evidence(inv, wanted_topics([d], topic_schema_names(inv), C))
    res = result_of(run(inv, ev, C, [d]), ID)
    assert res.status == "insufficient_evidence" and any(r.code == "recording_incomplete" for r in res.reasons)


def test_11_bare_status_topic(tmp_path):
    out, res, fs = go(tmp_path, [stream([el(l, "n") for l in (0, 1, 2, 3)])], typ=ST)
    assert sorted(f.observed_value["level_name"] for f in fs) == ["ERROR", "STALE", "WARN"]
    assert all(f.observed_value["element_index"] == 0 and f.observations[0].field_path == "level" and f.field_path_template == "level" for f in fs)


def test_11_bare_status_groups_runs(tmp_path):
    out, res, fs = go(tmp_path, [stream([el(1, "n")] * 4 + [el(0, "n")] + [el(1, "n")] * 2)], typ=ST)
    assert sorted(f.occurrence_count for f in fs) == [2, 4]


def test_12_stale_wording(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(el(3))])])
    (f,) = fs
    assert "STALE (3) was declared by the diagnostic source" in f.explanation and find_forbidden(f.explanation) == []
    assert "stale" not in f.explanation.lower().replace("level stale (3)", "")


def test_13_empty_hardware_id_is_known(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(el(1, "m", ""))] * 4)])
    (f,) = fs
    assert f.occurrence_count == 4 and f.observations[0].identity_state == "known"


def test_15_same_identity_on_two_topics(tmp_path):
    bag = X.write_typed_bag(tmp_path / "bag", [{"/d1": stream([arr(el(1))]), "/d2": stream([arr(el(1))])}], {"/d1": ARR, "/d2": ARR})
    out, ev, inv = run_detectors(bag, [DiagnosticsDetector()], C)
    assert len(out.findings) == 2 and {f.topic for f in out.findings} == {"/d1", "/d2"}


def test_16_run_across_segments_lists_both_sources(tmp_path):
    s1, s2 = stream([arr(el(1))]), stream([arr(el(1))], start=F.BASE + 10 * F.MS)
    out, res, fs = go(tmp_path, [s1, s2])
    (f,) = fs
    assert f.occurrence_count == 2 and len(f.source) == 2


def test_16_id_covers_every_artifact(tmp_path):
    s1 = PAD + stream([arr(el(1))], start=F.BASE + 100 * F.MS)
    s2 = PAD2 + stream([arr(el(1))], start=F.BASE + 300 * F.MS)
    ids = {n: go(tmp_path / n, [s1, s2], alternate=alt)[2][0].finding_id for n, alt in (("a", None), ("b", None), ("c", {0}), ("d", {1}))}
    assert ids["a"] == ids["b"] and len({ids["a"], ids["c"], ids["d"]}) == 3


def test_cap_exact_suppressed(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(el(1, ""))] * 5)], config=cfg({ID: {"enabled": True}}, {"max_findings_per_detector": 2}))
    assert len(fs) == 2 and res.findings_truncated.suppressed_exact_count == 3


def test_cap_counts_runs_not_occurrences(tmp_path):
    ps = [arr(el(1, "a"))] * 3 + [arr(el(0, "a"))] + [arr(el(1, "a"))] * 3 + [arr(el(0, "a"))] + [arr(el(1, "a"))] * 3
    out, res, fs = go(tmp_path, [stream(ps)], config=cfg({ID: {"enabled": True}}, {"max_findings_per_detector": 1}))
    assert len(fs) == 1 and res.findings_truncated.suppressed_exact_count == 2


def test_payload_stamp_is_secondary(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(el(1), stamp={"sec": 7, "nanosec": 1})])])
    (f,) = fs
    assert f.clock_domain == "mcap_log_time" and f.observations[0].payload_stamp.clock_domain == "payload_stamp:/diagnostics:header.stamp"


def test_unsupported_type_not_applicable(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(el(1))])], typ="my_pkg/msg/DiagnosticArray", config=cfg({ID: {"enabled": True, "topics": ["/diagnostics"]}}))
    assert res.status == "not_applicable"


def test_builtin_constants_flagged_for_json(tmp_path):
    out, res, fs = go(tmp_path, [stream([arr(el(2))])])
    assert any(n.code == "constants_from_builtin_table" for n in fs[0].uncertainty)


@needs_decoder
def test_cdr_array_embedded_constants_and_empty_name(tmp_path):
    from tests import cdr_fixtures as cf
    T = cf.typestore().types
    def mk(level, name, hw="h"):
        return T[ST](level=level, name=name, message="m", hardware_id=hw, values=[T["diagnostic_msgs/msg/KeyValue"](key="k", value="v")])
    def a(*els):
        return T[ARR](header=T["std_msgs/msg/Header"](stamp=T["builtin_interfaces/msg/Time"](sec=3, nanosec=4), frame_id=""), status=list(els))
    msgs = [(F.BASE, a(mk(0, "ok"), mk(1, "w"), mk(3, ""))), (F.BASE + F.MS, a(mk(1, "w")))]
    bag = X.write_cdr_bag(tmp_path / "bag", [[("/diagnostics", ARR, msgs)]])
    out, ev, inv = run_detectors(bag, [DiagnosticsDetector()], C)
    fs = out.findings
    assert sorted((f.observed_value["level_name"], f.occurrence_count) for f in fs) == [("STALE", 1), ("WARN", 2)]
    assert not any(n.code == "constants_from_builtin_table" for f in fs for n in f.uncertainty)
    stale = next(f for f in fs if f.observed_value["level_name"] == "STALE")
    assert stale.observations[0].identity_state == "empty" and stale.observations[0].payload_stamp.value_ns == 3_000_000_004
    w = next(f for f in fs if f.observed_value["level_name"] == "WARN")
    assert w.observed_value["values_first"] == [["k", "v"]]


@needs_decoder
def test_cdr_embedded_diag_constants_disagree_is_blind_spot(tmp_path):
    from tests import cdr_fixtures as cf
    T = cf.typestore().types
    schema = cf.schema_text(ARR).replace(b"WARN=1", b"WARN=5")
    m = T[ARR](header=T["std_msgs/msg/Header"](stamp=T["builtin_interfaces/msg/Time"](sec=3, nanosec=4), frame_id=""), status=[])
    bag = X.write_cdr_bag(tmp_path / "bag", [[("/diagnostics", ARR, [(F.BASE, m)])]], schema_override={ARR: schema})
    out, ev, inv = run_detectors(bag, [DiagnosticsDetector()], C)
    res = result_of(out, ID)
    assert res.status == "blind_spot" and res.reasons[0].code == "constants_mismatch"
