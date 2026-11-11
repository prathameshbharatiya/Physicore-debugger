"""PRE-01..16 and UNC-04 (TEST_PLAN 4). Seeded random instances are compared with the naive oracle; seeds are in the failure messages."""
import random
import time

import pytest
from pydantic import ValidationError

from physicore_collector.localization import precedence as P
from physicore_collector.localization import vocabulary as V
from physicore_collector.localization.intervals import ClockDomainError, Interval
from tests.localization.l1 import LOG, sym
from tests.localization.oracle import oracle_layers

SEED = 20261104


def ivs(spec: dict[str, tuple[int, int]], lane=LOG):
    return [(k, Interval(lane, a, b)) for k, (a, b) in spec.items()]


def lane_of(spec, *, blockers=(), truncated=()):
    return P.build_lane(LOG, [(k, Interval(LOG, a, b), k in truncated) for k, (a, b) in spec.items()], list(blockers))


def test_PRE_01_empty_lane():
    lane = P.build_lane(LOG, [], [])
    assert lane.symptom_count == 0 and lane.layers == [] and lane.earliest.defence == "none_observed" and lane.earliest.symptom_ids == []
    assert lane.earliest.statement == V.NONE_OBSERVED_SENTENCE


def test_PRE_01_empty_lane_keeps_its_blockers():
    assert P.build_lane(LOG, [], ["b1"]).earliest.blocker_ids == ["b1"]


def test_PRE_02_single_symptom_is_defended():
    lane = lane_of({"A": (5, 5)})
    assert lane.layers == [["A"]] and lane.earliest.symptom_ids == ["A"] and lane.earliest.defence == "defended" and lane.earliest.blocker_ids == []


def test_PRE_03_chain_layers():
    lane = lane_of({"A": (1, 1), "B": (5, 5), "C": (9, 9)})
    assert lane.layers == [["A"], ["B"], ["C"]] and lane.earliest.symptom_ids == ["A"]


def test_PRE_04_two_disjoint_earliest_are_tied():
    lane = lane_of({"A": (1, 1), "B": (1, 1), "C": (9, 9)})
    assert lane.earliest.defence == "tied" and lane.earliest.symptom_ids == ["A", "B"] and lane.earliest.statement == V.TIED_SENTENCE


def test_PRE_05_overlapping_symptoms_are_tied():
    lane = lane_of({"A": (0, 10), "B": (5, 6)})
    assert lane.layers == [["A", "B"]] and lane.earliest.defence == "tied"


def test_PRE_06_touching_symptoms_are_unordered():
    lane = lane_of({"A": (0, 5), "B": (5, 9)})
    assert lane.layers == [["A", "B"]] and lane.earliest.defence == "tied"


def test_PRE_08_interval_order_example():
    lane = lane_of({"A": (0, 1), "B": (0, 100), "C": (50, 60)})
    lm = P.layer_map(ivs({"A": (0, 1), "B": (0, 100), "C": (50, 60)}))
    assert lm == {"A": 0, "B": 0, "C": 1} and lane.layers == [["A", "B"], ["C"]]


def _random_spec(r, n, span=60, width=8):
    spec = {}
    for i in range(n):
        a = r.randint(0, span)
        spec[f"s{i:03d}"] = (a, a + r.randint(0, width))
    return spec


@pytest.mark.parametrize("n", [5, 20, 60])
def test_PRE_09_layers_equal_the_naive_oracle(n):
    r = random.Random(SEED + n)
    for k in range(200):
        spec = _random_spec(r, n)
        assert P.layer_map(ivs(spec)) == oracle_layers(spec), f"seed={SEED + n} k={k}"


def test_PRE_07_layer_property():
    r = random.Random(SEED)
    for k in range(150):
        spec = _random_spec(r, 30)
        lm = P.layer_map(ivs(spec))
        for x, (xl, xu) in spec.items():
            preds = [y for y, (yl, yu) in spec.items() if yu < xl]
            if lm[x] == 0:
                assert preds == [], f"seed={SEED} k={k}"
            else:
                assert any(lm[y] == lm[x] - 1 for y in preds), f"seed={SEED} k={k}"
            assert all(lm[y] < lm[x] for y in preds)
        for x in spec:                                                   # same layer => pairwise unordered
            for y in spec:
                if x != y and lm[x] == lm[y]:
                    assert not (spec[x][1] < spec[y][0]) and not (spec[y][1] < spec[x][0])


def test_PRE_10_input_order_shuffle_is_irrelevant():
    r = random.Random(SEED + 3)
    for k in range(100):
        spec = _random_spec(r, 40)
        items = list(spec.items())
        base = P.build_lane(LOG, [(s, Interval(LOG, a, b), False) for s, (a, b) in items], [])
        r.shuffle(items)
        again = P.build_lane(LOG, [(s, Interval(LOG, a, b), False) for s, (a, b) in items], [])
        assert base.model_dump() == again.model_dump(), f"seed={SEED + 3} k={k}"


def test_PRE_11_two_lanes_have_separate_earliest_and_a_cross_lane_statement():
    a = sym("A", 10, lane=LOG)
    b = sym("B", 5, lane="payload_stamp:/t:header.stamp")
    lanes, note = P.build_lanes([a, b])
    assert [l.domain for l in lanes] == [LOG, "payload_stamp:/t:header.stamp"] and note == V.NOT_COMPARED_ACROSS_LANES
    assert [l.earliest.symptom_ids for l in lanes] == [["A"], ["B"]] and all(l.earliest.defence == "defended" for l in lanes)
    assert P.build_lanes([a])[1] is None


def test_PRE_11_numerically_interleaved_lanes_are_never_ordered_against_each_other():
    a = [sym("A1", 100), sym("A2", 300)]
    b = [sym("B1", 200, lane="other")]
    lanes, _ = P.build_lanes(a + b)
    assert [l.layers for l in lanes] == [[["A1"], ["A2"]], [["B1"]]]


def test_PRE_11_a_symptom_with_a_secondary_placement_takes_part_in_both_lanes():
    s = sym("S", 50, secondary=[("payload_stamp:/t:header.stamp", 7, 7)])
    t = sym("T", 9, lane="payload_stamp:/t:header.stamp")
    lanes, _ = P.build_lanes([s, t])
    by = {l.domain: l for l in lanes}
    assert by[LOG].layers == [["S"]] and by["payload_stamp:/t:header.stamp"].layers == [["S"], ["T"]]
    assert by["payload_stamp:/t:header.stamp"].earliest.symptom_ids == ["S"]


def test_PRE_12_defended_statement_is_the_fixed_scope_sentence():
    assert lane_of({"A": (1, 1)}).earliest.statement == V.SCOPE_SENTENCE
    assert V.find_forbidden(V.SCOPE_SENTENCE) == [] and "cause" not in V.SCOPE_SENTENCE.lower()


def test_PRE_13_single_earliest_with_a_blocker_is_not_defended():
    lane = lane_of({"A": (1, 1)}, blockers=["b2", "b1", "b1"])
    assert lane.earliest.defence == "not_defended" and lane.earliest.blocker_ids == ["b1", "b2"] and lane.earliest.statement == V.NOT_DEFENDED_SENTENCE


def test_PRE_14_single_earliest_truncated_is_not_defended():
    lane = lane_of({"A": (1, 1), "B": (9, 9)}, truncated=["A"])
    assert lane.earliest.defence == "not_defended" and lane.earliest.blocker_ids == []


def test_PRE_14_a_truncated_later_symptom_does_not_downgrade_the_earliest():
    lane = lane_of({"A": (1, 1), "B": (9, 9)}, truncated=["B"])
    assert lane.earliest.defence == "defended"


def test_PRE_13_blockers_on_a_tied_lane_stay_listed_and_status_stays_tied():
    lane = lane_of({"A": (1, 1), "B": (1, 1)}, blockers=["b1"])
    assert lane.earliest.defence == "tied" and lane.earliest.blocker_ids == ["b1"]


def test_PRE_15_adding_a_later_unrelated_symptom_changes_nothing_about_the_earliest():
    r = random.Random(SEED + 5)
    for k in range(100):
        spec = _random_spec(r, 15)
        base = lane_of(spec).earliest
        spec2 = dict(spec)
        top = max(b for _a, b in spec.values())
        spec2["zzz"] = (top + 1 + r.randint(0, 5), top + 10)
        assert lane_of(spec2).earliest == base, f"seed={SEED + 5} k={k}"


def test_PRE_16_hundred_thousand_symptoms_complete_and_agree_with_the_oracle_on_a_subsample():
    r = random.Random(SEED + 6)
    n = 100_000
    spec = {f"s{i:06d}": (a, a + r.randint(0, 50)) for i, a in ((i, r.randint(0, 10**7)) for i in range(n))}
    t0 = time.monotonic()
    lm = P.layer_map(ivs(spec))
    elapsed = time.monotonic() - t0
    assert len(lm) == n and elapsed < 20                              # a generous guard; the measured time is reported by the benchmark in Step 9
    sub = {f"s{i:04d}": (a, a + r.randint(0, 80)) for i, a in ((i, r.randint(0, 2000)) for i in range(250))}      # dense: real chains, oracle stays cheap
    assert max(oracle_layers(sub).values()) >= 3
    assert P.layer_map(ivs(sub)) == oracle_layers(sub)


def test_PRE_16_all_overlapping_symptoms_form_one_layer_without_quadratic_blowup():
    n = 100_000
    spec = {f"s{i:06d}": (i, 10**9 + i) for i in range(n)}
    t0 = time.monotonic()
    lm = P.layer_map(ivs(spec))
    assert set(lm.values()) == {0} and time.monotonic() - t0 < 20


def test_UNC_04_widening_intervals_never_orders_more_or_makes_a_unique_earliest():
    r = random.Random(SEED + 7)
    for k in range(150):
        spec = _random_spec(r, 20)
        wide = {s: (a - r.randint(0, 5), b + r.randint(0, 5)) for s, (a, b) in spec.items()}
        for x in spec:
            for y in spec:
                if x != y and wide[x][1] < wide[y][0]:
                    assert spec[x][1] < spec[y][0], f"seed={SEED + 7} k={k}"          # ordered after widening => ordered before
        base, w = lane_of(spec).earliest, lane_of(wide).earliest
        if base.defence == "tied":
            assert w.defence == "tied", f"seed={SEED + 7} k={k}"


def test_layer_map_rejects_mixed_lanes_and_duplicate_ids():
    with pytest.raises(ValueError):
        P.layer_map([("a", Interval("x", 1, 1)), ("b", Interval("y", 2, 2))])
    with pytest.raises(ValueError):
        P.layer_map([("a", Interval(LOG, 1, 1)), ("a", Interval(LOG, 2, 2))])


def test_earliest_model_rejects_inconsistent_claims():
    base = dict(blocker_ids=[], statement="s")
    for bad in (dict(symptom_ids=["a"], defence="none_observed"), dict(symptom_ids=["a"], defence="tied"), dict(symptom_ids=[], defence="defended"),
                dict(symptom_ids=["a", "b"], defence="defended"), dict(symptom_ids=["a"], defence="defended", blocker_ids=["b"]),
                dict(symptom_ids=["a"], defence="maybe")):
        with pytest.raises(ValidationError):
            P.Earliest(**{**base, **bad})


def test_extra_lane_with_blockers_but_no_symptoms_is_reported():
    lanes, note = P.build_lanes([sym("A", 1)], {"other": ["b1"]}, extra_lanes=["other"])
    by = {l.domain: l for l in lanes}
    assert by["other"].earliest.defence == "none_observed" and by["other"].earliest.blocker_ids == ["b1"] and note is not None


def test_blockers_apply_only_to_their_lane():
    lanes, _ = P.build_lanes([sym("A", 1), sym("B", 1, lane="other")], {LOG: ["b1"]})
    by = {l.domain: l for l in lanes}
    assert by[LOG].earliest.defence == "not_defended" and by["other"].earliest.defence == "defended"


def test_lane_and_earliest_models_are_strict():
    with pytest.raises(ValidationError):
        P.Lane(domain="d", symptom_count=0, layers=[], earliest=P.Earliest(symptom_ids=[], defence="none_observed", blocker_ids=[], statement="s"), score=1)


def test_layer_members_are_sorted_by_id_not_by_onset():
    lane = lane_of({"Z": (0, 5), "A": (3, 9), "M": (4, 4)})
    assert lane.layers == [["A", "M", "Z"]] and lane.earliest.symptom_ids == ["A", "M", "Z"]


def test_truncated_symptom_reaches_the_lane_defence_through_build_lanes():
    lanes, _ = P.build_lanes([sym("A", 1, truncated=True), sym("B", 9)])
    assert lanes[0].earliest.defence == "not_defended" and lanes[0].earliest.blocker_ids == []
    lanes2, _ = P.build_lanes([sym("A", 1), sym("B", 9, truncated=True)])
    assert lanes2[0].earliest.defence == "defended"
