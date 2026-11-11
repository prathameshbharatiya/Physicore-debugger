import copy
import json
from fractions import Fraction
from pathlib import Path

import pytest
import yaml

from physicore_collector.anomaly.config import load_config, validate_config

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "anomaly" / "anomaly.example.yaml"

BASE = {
    "anomaly_config_version": 1,
    "limits": {"max_retained_observations": 5, "max_text_bytes": 64, "max_findings_per_detector": 100},
    "detectors": {
        "tierA.ros_log": {"enabled": True, "topics": "auto"},
        "tierA.diagnostics": {"enabled": True},
        "tierA.goal_status": {"enabled": True},
        "tierA.battery": {"enabled": True, "thresholds": {"percentage_below": "0.2", "percentage_scale": "fraction"}},
        "tierB.publishing_gap": {"enabled": True, "topics": ["/t"], "min_intervals": 10, "multiplier": "3", "absolute_floor_ns": 1000},
        "tierB.rate_change": {"enabled": True, "topics": ["/t"], "window_ns": 1000, "min_windows": 5, "min_baseline_count": 3,
                              "trim_start_windows": 0, "trim_end_windows": 0, "low_ratio": "0.5", "high_ratio": "2"},
        "tierB.stamp_order": {"enabled": True, "topics": ["/t"], "fields": ["header.stamp"]},
        "tierB.array_length": {"enabled": True, "rules": [{"topic": "/t", "path": "ranges", "expected_length": 3}]},
        "tierB.non_finite": {"enabled": True, "topics": ["/t"]},
    },
}


def cfg(**edits):
    d = copy.deepcopy(BASE)
    for path, val in edits.items():
        cur = d
        parts = path.split("/")
        for p in parts[:-1]:
            cur = cur[p]
        if val is KeyError:
            del cur[parts[-1]]
        else:
            cur[parts[-1]] = val
    return d


def problems(raw):
    r = validate_config(raw)
    return r.config, [f"{p.path}: {p.message}" for p in r.problems]


def invalid(raw, needle=None):
    c, p = problems(raw)
    assert c is None and p, "expected an invalid configuration"
    if needle:
        assert any(needle in x for x in p), p


def test_base_is_valid():
    c, p = problems(BASE)
    assert p == [] and c is not None and c.enabled("tierB.rate_change") and not c.enabled("nothing")
    assert c.parameters("tierB.publishing_gap")["multiplier"] == "3/1"


def test_CS_08_and_27_unknown_keys_rejected_at_every_level():
    invalid({**BASE, "extra": 1}, "extra")
    invalid(cfg(**{"limits/extra": 1}), "limits.extra")
    invalid(cfg(**{"detectors/tierB.publishing_gap/surprise": 1}), "surprise")
    invalid(cfg(**{"detectors/tierA.ros_log/grouping": "x"}), "grouping")
    invalid(cfg(**{"detectors/tierB.array_length/rules": [{"topic": "/t", "path": "p", "expected_length": 1, "bogus": 2}]}), "bogus")
    invalid(cfg(**{"detectors/tierB.mystery": {"enabled": True}}), "mystery")


def test_CS_18_late_message_key_is_rejected_with_the_deferral_message():
    raw = cfg(**{"detectors/tierB.late_message": {"enabled": True}})
    invalid(raw, "deferred")
    invalid(cfg(**{"detectors/clock_equivalence": []}), "clock_equivalence")


@pytest.mark.parametrize("det,key", [("tierB.publishing_gap", k) for k in ("topics", "min_intervals", "multiplier", "absolute_floor_ns")]
                         + [("tierB.rate_change", k) for k in ("topics", "window_ns", "min_windows", "min_baseline_count", "trim_start_windows", "trim_end_windows")]
                         + [("tierB.stamp_order", k) for k in ("topics", "fields")]
                         + [("tierB.array_length", "rules"), ("tierB.non_finite", "topics")])
def test_CS_09_26_missing_required_parameter_is_invalid(det, key):
    invalid(cfg(**{f"detectors/{det}/{key}": KeyError}), key)


@pytest.mark.parametrize("key", ["max_retained_observations", "max_text_bytes", "max_findings_per_detector"])
def test_CS_20_limits_are_required(key):
    invalid(cfg(**{f"limits/{key}": KeyError}), key)
    r = copy.deepcopy(BASE); del r["limits"]
    invalid(r, "limits")


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "5", None])
def test_CS_10_positive_ints_are_strict(bad):
    invalid(cfg(**{"detectors/tierB.publishing_gap/min_intervals": bad}), "min_intervals")
    invalid(cfg(**{"limits/max_text_bytes": bad}), "max_text_bytes")


def test_CS_10_trim_windows_accept_explicit_zero_but_not_negative():
    assert problems(cfg(**{"detectors/tierB.rate_change/trim_start_windows": 0}))[1] == []
    invalid(cfg(**{"detectors/tierB.rate_change/trim_end_windows": -1}), "trim_end_windows")
    invalid(cfg(**{"detectors/tierB.rate_change/trim_end_windows": True}), "trim_end_windows")


def test_CS_11_ratios_are_exact_decimal_strings():
    c, p = problems(cfg(**{"detectors/tierB.publishing_gap/multiplier": "1.5"}))
    assert p == [] and Fraction(c.parameters("tierB.publishing_gap")["multiplier"]) == Fraction(3, 2)
    for bad in ("abc", 1.5, 2, True, "1e3", "-1", "0", "0.0", "", None, "1,5", " 2"):
        invalid(cfg(**{"detectors/tierB.publishing_gap/multiplier": bad}), "multiplier")


def test_CS_12_hash_is_stable_under_reordering_and_comments(tmp_path):
    a = validate_config(BASE).config
    shuffled = json.loads(json.dumps(BASE))
    shuffled["detectors"] = dict(reversed(list(shuffled["detectors"].items())))
    shuffled["limits"] = dict(reversed(list(shuffled["limits"].items())))
    shuffled = dict(reversed(list(shuffled.items())))
    b = validate_config(shuffled).config
    p1 = tmp_path / "a.yaml"; p1.write_text("# a comment\n" + yaml.safe_dump(BASE))
    p2 = tmp_path / "b.yaml"; p2.write_text(yaml.safe_dump(shuffled, sort_keys=False) + "# trailing comment\n")
    assert a.config_hash == b.config_hash == load_config(p1).config.config_hash == load_config(p2).config.config_hash
    changed = validate_config(cfg(**{"limits/max_text_bytes": 65})).config
    assert changed.config_hash != a.config_hash
    ratio_same = validate_config(cfg(**{"detectors/tierB.publishing_gap/multiplier": "3.0"})).config
    assert ratio_same.config_hash == a.config_hash   # exact rational normalisation


def test_CS_13_tier_b_topics_cannot_be_auto():
    for det in ("tierB.publishing_gap", "tierB.rate_change", "tierB.stamp_order", "tierB.non_finite"):
        invalid(cfg(**{f"detectors/{det}/topics": "auto"}), "topics")


def test_tier_a_topics_auto_or_explicit_list():
    assert problems(cfg(**{"detectors/tierA.ros_log/topics": ["/rosout"]}))[1] == []
    invalid(cfg(**{"detectors/tierA.ros_log/topics": []}), "topics")
    invalid(cfg(**{"detectors/tierA.ros_log/topics": "all"}), "topics")


def test_CS_21_rate_both_ratios_omitted_is_invalid():
    r = cfg(**{"detectors/tierB.rate_change/low_ratio": KeyError})
    del r["detectors"]["tierB.rate_change"]["high_ratio"]
    invalid(r, "at least one of low_ratio / high_ratio")


def test_CS_22_23_24_single_or_both_ratios_are_valid():
    only_low = cfg(**{"detectors/tierB.rate_change/high_ratio": KeyError})
    c, p = problems(only_low)
    assert p == [] and "low_ratio" in c.parameters("tierB.rate_change") and "high_ratio" not in c.parameters("tierB.rate_change")
    only_high = cfg(**{"detectors/tierB.rate_change/low_ratio": KeyError})
    c, p = problems(only_high)
    assert p == [] and "high_ratio" in c.parameters("tierB.rate_change") and "low_ratio" not in c.parameters("tierB.rate_change")
    c, p = problems(BASE)
    assert p == [] and {"low_ratio", "high_ratio"} <= set(c.parameters("tierB.rate_change"))


@pytest.mark.parametrize("bad", [None, "", "0", "-1", "abc", 0.5, 2])
def test_CS_25_present_ratio_keys_must_hold_positive_decimal_strings(bad):
    invalid(cfg(**{"detectors/tierB.rate_change/low_ratio": bad}), "low_ratio")
    invalid(cfg(**{"detectors/tierB.rate_change/high_ratio": bad}), "high_ratio")


def test_CS_28_disabled_or_absent_detector_is_simply_not_enabled():
    c, p = problems(cfg(**{"detectors/tierB.rate_change": {"enabled": False}}))
    assert p == [] and not c.enabled("tierB.rate_change") and "tierB.rate_change" in c.disabled
    d = copy.deepcopy(BASE); del d["detectors"]["tierB.rate_change"]
    c, p = problems(d)
    assert p == [] and not c.enabled("tierB.rate_change") and "tierB.rate_change" not in c.disabled
    invalid(cfg(**{"detectors/tierB.rate_change": {"enabled": False, "surprise": 1}}), "surprise")   # unknown keys still rejected
    assert problems(cfg(**{"detectors/tierB.rate_change": {"enabled": False, "window_ns": 5}}))[1] == []   # a known key is fine while disabled


def test_enabled_flag_must_be_boolean():
    invalid(cfg(**{"detectors/tierB.rate_change/enabled": "yes"}), "enabled")


def test_CS_29_shipped_example_is_valid_and_marked():
    text = EXAMPLE.read_text()
    assert "EXAMPLE VALUES, NOT RECOMMENDATIONS" in text
    r = load_config(EXAMPLE)
    assert r.problems == [] and r.config is not None
    rate = r.config.parameters("tierB.rate_change")
    assert "low_ratio" in rate or "high_ratio" in rate
    assert "OPTIONAL" in text


def test_CS_30_conditional_requirements():
    invalid(cfg(**{"detectors/tierB.array_length/rules": []}), "rules")
    invalid(cfg(**{"detectors/tierB.array_length/rules": [{"topic": "/t", "path": "p"}]}), "exactly one")
    invalid(cfg(**{"detectors/tierB.array_length/rules": [{"topic": "/t", "path": "p", "baseline": "mode", "min_observations": 5}]}), "min_share")
    invalid(cfg(**{"detectors/tierB.array_length/rules": [{"topic": "/t", "path": "p", "expected_length": 3, "baseline": "mode"}]}), "exactly one")
    invalid(cfg(**{"detectors/tierB.array_length/rules": [{"topic": "/t", "path": "p", "baseline": "mode", "min_observations": 5, "min_share": "1.5"}]}), "min_share")
    ok = cfg(**{"detectors/tierB.array_length/rules": [{"topic": "/t", "path": "p", "baseline": "mode", "min_observations": 5, "min_share": "0.8"}]})
    assert problems(ok)[1] == []
    invalid(cfg(**{"detectors/tierA.battery/thresholds": {"percentage_below": "0.2"}}), "percentage_scale")
    invalid(cfg(**{"detectors/tierA.battery/thresholds": {"percentage_scale": "fraction"}}), "percentage_below")
    invalid(cfg(**{"detectors/tierA.battery/thresholds": {"percentage_below": "0.2", "percentage_scale": "tenths"}}), "percentage_scale")


def test_version_must_be_the_integer_one():
    for bad in (2, "1", True, None):
        invalid({**BASE, "anomaly_config_version": bad}, "anomaly_config_version")


def test_load_config_never_raises(tmp_path):
    assert load_config(tmp_path / "missing.yaml").problems
    p = tmp_path / "bad.yaml"; p.write_text("a: [unclosed")
    assert "not valid YAML" in load_config(p).problems[0].message
    p = tmp_path / "bad.json"; p.write_text("{nope")
    assert "not valid JSON" in load_config(p).problems[0].message
    p = tmp_path / "list.yaml"; p.write_text("- 1\n- 2\n")
    assert load_config(p).config is None
    p = tmp_path / "bin.yaml"; p.write_bytes(b"\xff\xfe\x00bad")
    assert load_config(p).problems
    p = tmp_path / "ok.json"; p.write_text(json.dumps(BASE))
    assert load_config(p).config is not None
