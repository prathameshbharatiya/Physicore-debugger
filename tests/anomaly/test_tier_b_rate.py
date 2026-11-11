"""RATE-* (TEST_PLAN 6). Counts per window are built exactly; an independent oracle recomputes the windows."""
import math
from fractions import Fraction

import pytest

from physicore_collector.anomaly.detectors.tier_b_rate import RateChangeDetector
from physicore_collector.anomaly.findings import find_forbidden
from tests import investigator_fixtures as F
from tests.anomaly import fixtures as X
from tests.anomaly.detector_support import cfg, result_of, run_detectors

ID = "tierB.rate_change"
TYPE = "sensor_msgs/msg/LaserScan"
W = 1_000_000_000
T0 = F.BASE


def conf(**kw):
    p = {"enabled": True, "topics": ["/t"], "window_ns": W, "min_windows": 3, "min_baseline_count": 2, "trim_start_windows": 0, "trim_end_windows": 0,
         "low_ratio": "0.5", "high_ratio": "2"}
    p.update({k: v for k, v in kw.items() if k != "limits"})
    for k in [k for k, v in p.items() if v is None]:
        del p[k]
    return cfg({ID: p}, kw.get("limits"))


def win(k, c, t0=T0):
    """c messages inside window k, evenly spread and strictly inside it."""
    if c == 0:
        return []
    step = W // (c + 1)
    return [t0 + k * W + (j + 1) * step for j in range(c)]


def from_counts(counts, anchor=True):
    """Messages whose per-window counts are exactly `counts`; the first message is placed AT t0 so windows align at t0."""
    t = []
    for k, c in enumerate(counts):
        t += win(k, c)
    if anchor and t:
        t[0] = T0
    return t


def go(tmp_path, times, *, config=None, extra=None, segments=None, alternate=None):
    segs = segments if segments is not None else [times]
    bags = [{"/t": [(x, {"i": i}) for i, x in enumerate(s)], **(extra or {})} for s in segs]
    types = {"/t": TYPE, **{k: TYPE for k in (extra or {})}}
    bag = X.write_typed_bag(tmp_path / "bag", bags, types, alternate=alternate)
    out, ev, inv = run_detectors(bag, [RateChangeDetector()], config or conf())
    return out, result_of(out, ID), out.findings


def oracle(times, window, ts, te, min_windows, min_base, lo, hi):
    """Independent restatement of DESIGN 6.3."""
    times = sorted(times)
    t0 = times[0]
    K = (times[-1] - t0) // window
    c = [0] * (K + 1)
    for x in times:
        c[(x - t0) // window] += 1
    ev = list(range(1 + ts, K - te))                         # windows 1+ts .. K-1-te
    if len(ev) < min_windows:
        return "few_windows", []
    B = sorted(c[k] for k in ev)[(len(ev) - 1) // 2]
    if B < min_base:
        return "small_baseline", []
    hits = []
    for name, r, test in (("low", lo, lambda x: x <= Fraction(lo) * B if lo else False), ("high", hi, lambda x: x >= Fraction(hi) * B if hi else False)):
        if r is None:
            continue
        ks = [k for k in ev if test(c[k])]
        runs = []
        for k in ks:
            if runs and runs[-1][-1] == k - 1:
                runs[-1].append(k)
            else:
                runs.append([k])
        hits += [(name, r_[0], len(r_), min(c[k] for k in r_), max(c[k] for k in r_)) for r_ in runs]
    return "ok", sorted(hits)


def summary(fs):
    return sorted((f.observed_value["direction"], (f.observed_value["first_window_start_ns"] - T0) // W, f.observed_value["windows"],
                   f.observed_value["min_count"], f.observed_value["max_count"]) for f in fs)


def test_01_constant_rate(tmp_path):
    out, res, fs = go(tmp_path, from_counts([10] * 12))
    assert res.status == "no_anomaly_observed" and fs == []


def test_02_step_down_one_merged_finding(tmp_path):
    counts = [10] * 5 + [2] * 5 + [10] * 5
    out, res, fs = go(tmp_path, from_counts(counts))
    (f,) = fs
    assert f.observed_value["direction"] == "low" and f.observed_value["windows"] == 5 and f.observed_value["min_count"] == 2 == f.observed_value["max_count"]
    assert f.observed_value["baseline_count"] == 10 and f.occurrence_count == 5
    assert f.first_observed_at.value_ns == T0 + 5 * W and f.last_observed_at.value_ns == T0 + 9 * W
    assert f.severity == "notice" and f.anomaly_category == "rate_change" and f.expected_value == 10 and f.expected_source == "recording_baseline"
    assert f.threshold.name == "low_ratio" and f.threshold.value == "1/2" and f.clock_domain == "mcap_log_time"
    assert any(n.code == "baseline_from_recording" for n in f.uncertainty)


def test_03_step_up(tmp_path):
    out, res, fs = go(tmp_path, from_counts([10] * 8 + [30] * 3 + [10] * 5))
    (f,) = fs
    assert f.observed_value["direction"] == "high" and f.observed_value["windows"] == 3 and f.observed_value["min_count"] == 30


@pytest.mark.parametrize("c,low_hit,high_hit", [(4, True, False), (5, True, False), (6, False, False), (19, False, False), (20, False, True), (21, False, True)])
def test_04_inclusive_boundaries(tmp_path, c, low_hit, high_hit):
    counts = [10] * 6 + [c] + [10] * 6
    out, res, fs = go(tmp_path, from_counts(counts))
    got = {f.observed_value["direction"] for f in fs}
    assert ("low" in got) is low_hit and ("high" in got) is high_hit
    assert summary(fs) == oracle(from_counts(counts), W, 0, 0, 3, 2, "1/2", "2")[1] and (len(fs) == (low_hit or high_hit))


def test_04_fractional_ratio_exact(tmp_path):
    # B = 7, low_ratio 0.7 -> 4.9 exactly: count 4 is a hit, 5 is not
    out, res, fs = go(tmp_path / "a", from_counts([7] * 6 + [4] + [7] * 6), config=conf(low_ratio="0.7", high_ratio=None))
    assert len(fs) == 1
    out, res, fs = go(tmp_path / "b", from_counts([7] * 6 + [5] + [7] * 6), config=conf(low_ratio="0.7", high_ratio=None))
    assert fs == []


def test_05_alignment_jitter_no_false_finding(tmp_path):
    p = 100 * F.MS
    times = [T0 + i * p for i in range(200)]
    out, res, fs = go(tmp_path, times, config=conf(window_ns=950 * F.MS, min_baseline_count=5))
    assert fs == [] and res.status == "no_anomaly_observed"
    assert oracle(times, 950 * F.MS, 0, 0, 3, 5, "1/2", "2")[1] == []


RAMP_START = [10, 3, 5] + [10] * 10
RAMP_END = [10] * 10 + [5, 3, 10]


def test_06_startup_ramp_and_trim(tmp_path):
    t = from_counts(RAMP_START)
    out, res, fs = go(tmp_path / "a", t, config=conf(trim_start_windows=0))
    assert summary(fs) == oracle(t, W, 0, 0, 3, 2, "1/2", "2")[1] == [("low", 1, 2, 3, 5)]
    out, res, fs = go(tmp_path / "b", t, config=conf(trim_start_windows=2))
    assert fs == [] and res.status == "no_anomaly_observed"


def test_07_shutdown_ramp_and_trim(tmp_path):
    t = from_counts(RAMP_END)
    out, res, fs = go(tmp_path / "a", t, config=conf(trim_end_windows=0))
    assert summary(fs) == oracle(t, W, 0, 0, 3, 2, "1/2", "2")[1] and len(fs) == 1
    out, res, fs = go(tmp_path / "b", t, config=conf(trim_end_windows=2))
    assert fs == []


def test_08_first_and_last_windows_always_discarded(tmp_path):
    counts = [1] + [10] * 8 + [1]
    out, res, fs = go(tmp_path, from_counts(counts))
    assert fs == [] and res.status == "no_anomaly_observed"
    # 10 windows: first and last discarded -> 8 evaluated
    out, res, fs = go(tmp_path / "b", from_counts(counts), config=conf(min_windows=8))
    assert res.status == "no_anomaly_observed"
    out, res, fs = go(tmp_path / "c", from_counts(counts), config=conf(min_windows=9))
    assert res.status == "insufficient_evidence"


@pytest.mark.parametrize("mw,status", [(8, "no_anomaly_observed"), (9, "insufficient_evidence")])
def test_09_min_windows_boundary(tmp_path, mw, status):
    out, res, fs = go(tmp_path, from_counts([10] * 10), config=conf(min_windows=mw))
    assert res.status == status
    if status != "no_anomaly_observed":
        assert any(r.code == "too_few_windows" for r in res.reasons)


@pytest.mark.parametrize("mb,status", [(5, "no_anomaly_observed"), (6, "insufficient_evidence")])
def test_10_min_baseline_boundary(tmp_path, mb, status):
    out, res, fs = go(tmp_path, from_counts([5] * 12), config=conf(min_baseline_count=mb))
    assert res.status == status
    if status != "no_anomaly_observed":
        assert any(r.code == "baseline_count_too_small" for r in res.reasons)


def test_11_empty_windows_inside_span_count_as_zero(tmp_path):
    counts = [10, 10, 10, 0, 0, 10, 10, 10, 10]
    t = from_counts(counts)
    out, res, fs = go(tmp_path, t)
    (f,) = fs
    assert (f.observed_value["direction"], f.observed_value["windows"], f.observed_value["min_count"], f.observed_value["max_count"]) == ("low", 2, 0, 0)
    assert summary(fs) == oracle(t, W, 0, 0, 3, 2, "1/2", "2")[1]


def test_12_silence_after_last_message_creates_no_window(tmp_path):
    out, res, fs = go(tmp_path, from_counts([10] * 8), extra={"/other": [(T0 + i * W, {}) for i in range(60)]})
    assert fs == [] and res.status == "no_anomaly_observed"


def test_13_majority_at_new_rate_contaminates(tmp_path):
    counts = [10] + [10] * 4 + [2] * 7 + [10]
    out, res, fs = go(tmp_path, from_counts(counts))
    assert fs and all(any(n.code == "baseline_contaminated_possible" for n in f.uncertainty) for f in fs)
    assert {f.observed_value["direction"] for f in fs} == {"high"} and fs[0].observed_value["baseline_count"] == 2


def test_14_gradual_drift_no_finding(tmp_path):
    counts = [20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10, 9, 8]
    out, res, fs = go(tmp_path, from_counts(counts))
    assert fs == []


def test_15_log_time_only_payload_stamps_irrelevant(tmp_path):
    t = from_counts([10] * 5 + [2] * 5 + [10] * 5)
    bag = X.write_typed_bag(tmp_path / "bag", [{"/t": [(x, {"header": {"stamp": {"sec": i * 7, "nanosec": 0}}}) for i, x in enumerate(t)]}], {"/t": TYPE})
    out, ev, inv = run_detectors(bag, [RateChangeDetector()], conf())
    assert summary(out.findings) == [("low", 5, 5, 2, 2)] and out.decode_counter.total == 0


FIX16 = [10] * 3 + [2] * 2 + [10] * 5 + [25] * 2 + [10] * 3 + [10]


def test_16_directions_independent(tmp_path):
    t = from_counts(FIX16)
    out, res, fs = go(tmp_path / "a", t, config=conf(high_ratio=None))
    assert summary(fs) == [("low", 3, 2, 2, 2)]
    out, res, fs = go(tmp_path / "b", t, config=conf(low_ratio=None))
    assert summary(fs) == [("high", 10, 2, 25, 25)]
    out, res, fs = go(tmp_path / "c", t)
    assert summary(fs) == [("high", 10, 2, 25, 25), ("low", 3, 2, 2, 2)] == oracle(t, W, 0, 0, 3, 2, "1/2", "2")[1]


def test_17_incomplete_recording(tmp_path):
    from physicore_collector.anomaly.runner import run, topic_schema_names, wanted_topics
    from physicore_collector.inventory import inspect_evidence
    from physicore_collector.investigator.observations import load_evidence
    t = from_counts([10] * 12)
    bag = X.write_typed_bag(tmp_path / "bag", [{"/t": [(x, {}) for x in t]}, {"/t": [(T0 + 20 * W, {})]}], {"/t": TYPE})
    inv = inspect_evidence(bag)
    (bag / "bag_1.mcap").write_bytes((bag / "bag_1.mcap").read_bytes() + b"y")
    d = RateChangeDetector()
    ev = load_evidence(inv, wanted_topics([d], topic_schema_names(inv), conf()))
    res = result_of(run(inv, ev, conf(), [d]), ID)
    assert not ev.complete and res.status == "insufficient_evidence" and any(r.code == "recording_incomplete" for r in res.reasons)


def test_18_alignment_independent_of_other_topics(tmp_path):
    t = from_counts([10] * 5 + [2] * 5 + [10] * 5)
    a = go(tmp_path / "a", t)[2]
    b = go(tmp_path / "b", t, extra={"/zzz": [(T0 - 3 * W + 7, {}), (T0 + 99 * W, {})]})[2]
    assert summary(a) == summary(b)


def test_windows_are_half_open_at_boundaries(tmp_path):
    # a message exactly at t0 + k*W belongs to window k
    t = [T0 + i * W for i in range(15)]            # exactly one message per window, each ON a boundary
    counts_hit = go(tmp_path, t, config=conf(min_baseline_count=1))[2]
    assert counts_hit == []                          # one per window: every window equal -> no anomaly


def test_alignment_at_first_captured_message(tmp_path):
    shift = 123_456_789
    a = go(tmp_path / "a", [x + shift for x in from_counts([10] * 5 + [2] * 5 + [10] * 5)])[2]
    b = go(tmp_path / "b", from_counts([10] * 5 + [2] * 5 + [10] * 5))[2]
    assert [(f.observed_value["windows"], f.observed_value["min_count"]) for f in a] == [(f.observed_value["windows"], f.observed_value["min_count"]) for f in b]
    assert a[0].observed_value["first_window_start_ns"] - shift == b[0].observed_value["first_window_start_ns"]


def test_separate_runs_separate_findings(tmp_path):
    counts = [10] * 4 + [2] + [10] * 3 + [2, 2] + [10] * 4
    out, res, fs = go(tmp_path, from_counts(counts))
    assert summary(fs) == [("low", 4, 1, 2, 2), ("low", 8, 2, 2, 2)]
    assert len({f.finding_id for f in fs}) == 2


def test_cap_exact(tmp_path):
    counts = [10, 10] + [10, 2] * 6 + [10, 10, 10]
    out, res, fs = go(tmp_path, from_counts(counts), config=conf(limits={"max_findings_per_detector": 2}))
    assert len(fs) == 2 and res.findings_truncated.suppressed_exact_count == 4


def test_retained_observations_and_counts_capped(tmp_path):
    out, res, fs = go(tmp_path, from_counts([10] * 3 + [1] * 8 + [10] * 14), config=conf(limits={"max_retained_observations": 2}))
    (f,) = fs
    assert f.observed_value["windows"] == 8 and len(f.observed_value["window_counts"]) == 2 and len(f.observations) <= 2


def test_every_segment_is_a_contributing_artifact(tmp_path):
    t = from_counts([10] * 5 + [2] * 5 + [10] * 5)
    cut = [x for x in t if x < T0 + 8 * W]
    rest = [x for x in t if x >= T0 + 8 * W]
    ids = {}
    for n, alt in (("a", None), ("b", None), ("c", {0}), ("d", {1})):
        out, res, fs = go(tmp_path / n, None, segments=[cut, rest], alternate=alt)
        (f,) = fs
        ids[n] = f.finding_id
        assert len(f.source) == 2
    assert ids["a"] == ids["b"] and len({ids["a"], ids["c"], ids["d"]}) == 3


def test_wording(tmp_path):
    out, res, fs = go(tmp_path, from_counts([10] * 5 + [2] * 5 + [10] * 5))
    e = fs[0].explanation.lower()
    assert find_forbidden(fs[0].explanation) == [] and "dropout" not in e and "restart" not in e


def test_topic_with_no_messages(tmp_path):
    out, res, fs = go(tmp_path, [])
    assert res.status == "insufficient_evidence"
