"""TA-BAT-* (TEST_PLAN 4)."""
import pytest

from physicore_collector.anomaly.detectors.tier_a_battery import BatteryDetector
from physicore_collector.investigator.cdr import decoder_info
from tests import investigator_fixtures as F
from tests.anomaly import fixtures as X
from tests.anomaly.detector_support import cfg, result_of, run_detectors

TYPE = "sensor_msgs/msg/BatteryState"
ID = "tierA.battery"
C = cfg({ID: {"enabled": True}})
needs_decoder = pytest.mark.skipif(not decoder_info().available, reason="decoder extra not installed")


def b(health=1, serial="S1", location="slot0", **kw):
    d = {"voltage": 12.5, "percentage": 0.8, "power_supply_status": 2, "power_supply_health": health, "power_supply_technology": 3, "present": True}
    if serial is not ...:
        d["serial_number"] = serial
    if location is not ...:
        d["location"] = location
    d.update(kw)
    return {k: v for k, v in d.items() if v is not ...}


def stream(payloads, start=F.BASE, step=F.MS):
    return [(start + i * step, p) for i, p in enumerate(payloads)]


PAD = stream([b(1, "S9") for i in range(40)])
PAD2 = stream([b(1, "S8", percentage=0.5 + i / 1000) for i in range(40)], start=F.BASE + 200 * F.MS)


def go(tmp_path, segments, *, config=C, alternate=None, typ=TYPE):
    bag = X.write_typed_bag(tmp_path / "bag", [{"/battery": s} for s in segments], {"/battery": typ}, alternate=alternate)
    out, ev, inv = run_detectors(bag, [BatteryDetector()], config)
    return out, result_of(out, ID), out.findings


def test_01_health_values(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(h, serial=f"S{h}") for h in range(9)])])
    assert sorted(f.observed_value["health"] for f in fs) == [2, 3, 4, 5, 6, 7, 8]
    assert all(f.severity == "warning" and f.severity_basis == "detector_policy" and f.anomaly_category == "power_status" for f in fs)
    assert {f.observed_value["health_name"] for f in fs} == {"OVERHEAT", "DEAD", "OVERVOLTAGE", "UNSPEC_FAILURE", "COLD", "WATCHDOG_TIMER_EXPIRE", "SAFETY_TIMER_EXPIRE"}


def test_02_unrecognised_health(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(9), b(255)])])
    assert fs == [] and res.unrecognised_values == {"255": 1, "9": 1} and res.status == "insufficient_evidence"


def test_03_14_status_is_context_only(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(1, power_supply_status=s) for s in (0, 1, 2, 3, 4)])])
    assert fs == [] and res.status == "no_anomaly_observed"


def test_04_unknown_throughout(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(0)] * 5)])
    assert res.status == "insufficient_evidence" and any(r.code == "health_unknown_throughout" for r in res.reasons)
    assert any(r.code == "health_unknown_messages" and r.count == 5 for r in res.reasons)


def test_04_some_unknown_some_good_is_no_anomaly(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(0), b(1)])])
    assert res.status == "no_anomaly_observed" and any(r.code == "health_unknown_messages" for r in res.reasons)


def test_05_run_with_context(tmp_path):
    ps = [b(2, power_supply_status=2, voltage=12.0)] + [b(2)] * 98 + [b(2, power_supply_status=3, voltage=11.0, present=False)]
    out, res, fs = go(tmp_path, [stream(ps)])
    (f,) = fs
    assert f.occurrence_count == 100 and f.observed_value["context_first"]["voltage"] == "12.0" and f.observed_value["context_last"]["voltage"] == "11.0"
    assert f.observed_value["context_last"]["present"] is False and f.observed_value["context_first"]["power_supply_status"] == 2
    assert f.observed_value["context_last"]["power_supply_status"] == 3


def test_05_flag_change_starts_new_finding(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(2)] * 3 + [b(3)] * 2 + [b(1)] + [b(3)] * 4)])
    assert sorted((f.observed_value["health"], f.occurrence_count) for f in fs) == [(2, 3), (3, 2), (3, 4)]


def test_06_technology_has_no_effect(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(1, power_supply_technology=t) for t in (3, 7, 8)])])
    assert fs == [] and res.status == "no_anomaly_observed"


def test_07_present_false_context_only(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(1, present=False)])])
    assert fs == []


T = {"percentage_below": "0.2", "percentage_scale": "fraction"}
CT = cfg({ID: {"enabled": True, "thresholds": T}})


def test_08_threshold_strict_and_nan(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(1, percentage=p) for p in (0.2, 0.19, 0.2)])], config=CT)
    (f,) = fs
    assert f.observed_value["value_first"] == "0.19" and f.severity == "warning" and f.severity_basis == "config"
    assert f.threshold.name == "percentage_below" and f.threshold.value == "1/5" and f.threshold.unit == "fraction" and f.threshold.source == "config"
    out, res, fs = go(tmp_path / "n", [stream([b(1, percentage="nan")] if False else [b(1, percentage=0.5)])], config=CT)
    assert fs == []


def test_08_threshold_exact_boundary_decimal(tmp_path):
    ps = [b(1, percentage=0.2), b(1, percentage=0.1999999999999999)]
    out, res, fs = go(tmp_path, [stream(ps)], config=CT)
    assert len(fs) == 1 and fs[0].observed_value["value_first"].startswith("0.19999")


def test_08_threshold_run_breaks_when_not_below(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(1, percentage=p) for p in (0.1, 0.1, 0.5, 0.1)])], config=CT)
    assert sorted(f.occurrence_count for f in fs) == [1, 2]


def test_voltage_threshold(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(1, voltage=v) for v in (11.0, 9.5, 9.0)])], config=cfg({ID: {"enabled": True, "thresholds": {"voltage_below": "10"}}}))
    (f,) = fs
    assert f.occurrence_count == 2 and f.threshold.name == "voltage_below" and f.threshold.value == "10" and f.field_path == "voltage"


def test_health_flags_can_be_disabled(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(2), b(1, percentage=0.1)])], config=cfg({ID: {"enabled": True, "health_flags": False, "thresholds": T}}))
    assert [f.observed_value.get("health") for f in fs] == [None] and fs[0].threshold is not None


def test_10_wrong_types_and_missing(tmp_path):
    ps = [b("2"), b(2.0), b(True), b(None), b(...)]
    out, res, fs = go(tmp_path, [stream(ps)])
    assert fs == [] and res.payload_malformed_field == 5 and res.status == "insufficient_evidence"


def test_10_threshold_field_wrong_type(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(1, percentage="0.1"), b(1, percentage=True), b(1, percentage=None), b(1, percentage=...)])], config=CT)
    assert fs == [] and res.payload_malformed_field == 4


def test_11_nonfinite_voltage_with_good_health(tmp_path):
    # JSON has no NaN literal here (the accepted loader refuses it), so this is the numeric-only part: no Tier A finding for healthy data
    out, res, fs = go(tmp_path, [stream([b(1, voltage=0.0)])])
    assert fs == []


def test_12_two_batteries_interleaved(tmp_path):
    ps = [b(2, "A"), b(2, "B"), b(2, "A"), b(2, "B"), b(2, "A")]
    out, res, fs = go(tmp_path, [stream(ps)])
    assert sorted((f.observed_value["serial_number"], f.occurrence_count) for f in fs) == [("A", 3), ("B", 2)]


def test_13_both_empty_topic_level_run(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(3, "", "")] * 10)])
    (f,) = fs
    assert f.occurrence_count == 10 and any(n.code == "battery_identity_fields_empty" for n in f.uncertainty)
    assert not any(n.code == "identity_unknown" for n in f.uncertainty)


def test_15_absent_serial_per_occurrence(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(2, ...)] * 3)])
    assert len(fs) == 3 and all(f.occurrence_count == 1 and f.observations[0].identity_state == "absent" for f in fs)
    assert res.identity_field_malformed == {"serial_number": 3}
    assert all(any(n.code == "identity_unknown" for n in f.uncertainty) for f in fs)


def test_15_absent_location_and_malformed_serial(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(2, "S", ...), b(2, 5, "L")])])
    assert len(fs) == 2 and res.identity_field_malformed == {"location": 1, "serial_number": 1}


def test_16_empty_serial_nonempty_location_known(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(2, "", "slot")] * 4)])
    (f,) = fs
    assert f.occurrence_count == 4 and not any(n.code in ("battery_identity_fields_empty", "identity_unknown") for n in f.uncertainty)


def test_unhealthy_with_malformed_identity_still_evaluated_by_threshold_too(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(2, ..., percentage=0.1)])], config=cfg({ID: {"enabled": True, "thresholds": T}}))
    assert len(fs) == 2 and {f.severity_basis for f in fs} == {"detector_policy", "config"}


def test_two_segments_source_and_id(tmp_path):
    s1 = PAD + stream([b(2, "S1")], start=F.BASE + 100 * F.MS)
    s2 = PAD2 + stream([b(2, "S1")], start=F.BASE + 300 * F.MS)
    r = {n: go(tmp_path / n, [s1, s2], alternate=alt)[2] for n, alt in (("a", None), ("b", None), ("c", {0}), ("d", {1}))}
    (f,) = r["a"]
    assert f.occurrence_count == 2 and len(f.source) == 2
    ids = {n: v[0].finding_id for n, v in r.items()}
    assert ids["a"] == ids["b"] and len({ids["a"], ids["c"], ids["d"]}) == 3


def test_cap_and_exact_suppressed(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(2, ...)] * 5)], config=cfg({ID: {"enabled": True}}, {"max_findings_per_detector": 2}))
    assert len(fs) == 2 and res.findings_truncated.suppressed_exact_count == 3


def test_builtin_constants_flag_for_json(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(2)])])
    assert any(n.code == "constants_from_builtin_table" for n in fs[0].uncertainty)


def test_payload_stamp_secondary(tmp_path):
    out, res, fs = go(tmp_path, [stream([b(2, header={"stamp": {"sec": 9, "nanosec": 1}, "frame_id": ""})])])
    ps = fs[0].observations[0].payload_stamp
    assert ps.value_ns == 9_000_000_001 and fs[0].clock_domain == "mcap_log_time"


@needs_decoder
def test_cdr_battery_embedded_constants_and_nan(tmp_path):
    from tests import cdr_fixtures as cf
    T_ = cf.typestore().types
    m = cf.battery(F.BASE, 12.0, float("nan"), present=False, serial="")
    m.power_supply_health = 4
    m.location = ""
    bag = X.write_cdr_bag(tmp_path / "bag", [[("/battery", TYPE, [(F.BASE, m)])]])
    out, ev, inv = run_detectors(bag, [BatteryDetector()], CT)
    fs = out.findings
    assert len(fs) == 1 and fs[0].observed_value["health_name"] == "OVERVOLTAGE"        # NaN percentage is never a threshold finding
    assert any(n.code == "battery_identity_fields_empty" for n in fs[0].uncertainty)
    assert fs[0].observed_value["context_first"]["percentage"] == "nan" and fs[0].observed_value["context_first"]["present"] is False
    assert not any(n.code == "constants_from_builtin_table" for n in fs[0].uncertainty)


@needs_decoder
def test_cdr_iron_style_technology_constants_do_not_matter(tmp_path):
    from tests import cdr_fixtures as cf
    schema = cf.schema_text(TYPE) + b"\nuint8 POWER_SUPPLY_TECHNOLOGY_TERNARY=7\nuint8 POWER_SUPPLY_TECHNOLOGY_VRLA=8\n"
    m = cf.battery(F.BASE, 12.0, 0.5)
    m.power_supply_health = 5
    bag = X.write_cdr_bag(tmp_path / "bag", [[("/battery", TYPE, [(F.BASE, m)])]], schema_override={TYPE: schema})
    out, ev, inv = run_detectors(bag, [BatteryDetector()], C)
    assert len(out.findings) == 1 and out.findings[0].observed_value["health_name"] == "UNSPEC_FAILURE"


@needs_decoder
def test_cdr_embedded_health_constant_mismatch_is_blind_spot(tmp_path):
    from tests import cdr_fixtures as cf
    schema = cf.schema_text(TYPE).replace(b"POWER_SUPPLY_HEALTH_DEAD=3", b"POWER_SUPPLY_HEALTH_DEAD=30")
    bag = X.write_cdr_bag(tmp_path / "bag", [[("/battery", TYPE, [(F.BASE, cf.battery(F.BASE, 12.0, 0.5))])]], schema_override={TYPE: schema})
    out, ev, inv = run_detectors(bag, [BatteryDetector()], C)
    assert result_of(out, ID).status == "blind_spot"
