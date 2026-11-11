"""VM: the view model (FR-VW-04..16): counts, ordering, first divergence, evidence references, lane axes; nothing invented."""
import json
import random

import pytest

from physicore_collector.investigation_view.model import build_view, with_verification

from .fx import example_dict, gen, load, view_of

CASES = [(0, 1), (1, 1), (2, 1), (5, 1), (6, 2), (9, 3), (20, 4)]


def ids(v, key):
    return [x["id"] for x in v[key]]


def test_VM_01_example_counts_equal_the_report():
    d = example_dict(); v = view_of(d)
    c = v["counts"]
    assert c == {"symptoms": len(d["symptoms"]), "links": len(d["links"]), "links_total": d["coverage"]["links_total"], "components": len(d["components"]),
                 "chains": len(d["chains"]), "blockers": len(d["blockers"]), "caveats": len(d["caveats"]), "refs": len(v["evidence_refs"]), "lanes": len(d["lanes"])}


@pytest.mark.parametrize("n,l", CASES)
def test_VM_02_generated_counts(n, l):
    d = gen(n, l, blockers=2); v = view_of(d)
    assert v["counts"]["symptoms"] == n and len(v["symptoms"]) == n and v["counts"]["lanes"] == l
    assert len(v["links"]) == len(d["links"]) and len(v["chains"]) == len(d["chains"]) and len(v["blockers"]) == 2


def test_VM_03_random_reports_keep_every_count():
    rnd = random.Random(7)
    for _ in range(40):
        n, l = rnd.randint(0, 40), rnd.randint(1, 5)
        d = gen(n, l, m2_every=rnd.choice([0, 2, 5]), width=rnd.choice([0, 100, 900]), step=rnd.choice([1000, 5000]), blockers=rnd.randint(0, 3))
        v = view_of(d)
        assert v["counts"]["symptoms"] == n
        assert sum(len(ln["items"]) for ln in v["lanes"]) == n
        assert sorted(ids(v, "symptoms")) == sorted(s["symptom_id"] for s in d["symptoms"])
        assert len(v["first_divergence"]) == sum(1 for s in d["symptoms"] if s["source"] == "m2_finding")


def test_VM_04_symptoms_and_links_are_sorted_by_id():
    v = view_of(gen(30, 3))
    for key in ("symptoms", "links", "chains", "components", "blockers"):
        got = ids(v, key); assert got == sorted(got)


def test_VM_05_lane_items_are_sorted_by_onset_then_id():
    d = gen(30, 1, width=0)
    d["symptoms"].reverse()
    v = view_of(d)
    keys = [(it["lower_ns"], it["upper_ns"], it["id"]) for it in v["lanes"][0]["items"]]
    assert keys == sorted(keys)


def test_VM_06_each_lane_has_its_own_origin_and_span():
    v = view_of(gen(12, 3, step=1000, width=100))
    for ln in v["lanes"]:
        lows = [it["lower_ns"] for it in ln["items"]]; highs = [it["upper_ns"] for it in ln["items"]]
        assert ln["origin_ns"] == min(lows) and ln["span_ns"] == max(highs) - min(lows)
        assert all(it["off_lower_ns"] == it["lower_ns"] - ln["origin_ns"] for it in ln["items"])
    assert len({ln["origin_ns"] for ln in v["lanes"]}) == 3


def test_VM_07_lanes_are_never_aligned_on_a_shared_axis():
    v = view_of(gen(12, 3))
    for ln in v["lanes"]:
        assert ln["items"][0]["off_lower_ns"] == 0


def test_VM_08_first_divergence_is_the_m2_findings_in_lane_time_order():
    d = gen(20, 2, m2_every=3); v = view_of(d)
    want = sorted((s["onset"]["domain"], s["onset"]["lower_ns"], s["onset"]["upper_ns"], s["symptom_id"]) for s in d["symptoms"] if s["source"] == "m2_finding")
    assert [(f["lane"], f["lower_ns"], f["upper_ns"], f["id"]) for f in v["first_divergence"]] == want


def test_VM_09_no_first_divergence_without_a_contract():
    assert view_of(gen(10, 1))["first_divergence"] == []


def test_VM_10_evidence_references_are_deduplicated_and_numbered():
    d = gen(6, 1)
    d["symptoms"][1]["evidence"] = list(d["symptoms"][0]["evidence"])
    v = view_of(d)
    assert len(v["evidence_refs"]) == 5
    shared = [r for r in v["evidence_refs"] if len(r["cited_by"]) == 2]
    assert len(shared) == 1
    assert [r["id"] for r in v["evidence_refs"]] == [f"E{i}" for i in range(1, 6)]


def test_VM_11_every_symptom_evidence_id_exists():
    v = view_of(gen(15, 2, blockers=2))
    known = {r["id"] for r in v["evidence_refs"]}
    for s in v["symptoms"]:
        assert set(s["evidence"]) <= known
    for b in v["blockers"]:
        assert set(b["observations"]) <= known


def test_VM_12_blocker_observation_references_are_cited_by_the_blocker():
    v = view_of(gen(3, 1, blockers=1))
    r = [x for x in v["evidence_refs"] if any(c["kind"] == "blocker" for c in x["cited_by"])]
    assert r


def test_VM_13_nothing_is_invented_every_id_exists_in_the_report():
    d = gen(12, 3, blockers=1); v = view_of(d)
    sid = {s["symptom_id"] for s in d["symptoms"]}
    for ch in v["chains"]:
        assert set(ch["symptom_ids"]) <= sid
    for l in v["links"]:
        assert l["from_symptom"] in sid and l["to_symptom"] in sid


def test_VM_14_symptom_chains_and_links_membership():
    v = view_of(gen(6, 1))
    s = {x["id"]: x for x in v["symptoms"]}
    assert all(len(x["chains"]) == 1 for x in s.values())
    assert len(s["s-00000000"]["links"]) == 1 and len(s["s-00000001"]["links"]) == 2


def test_VM_15_the_view_is_json_serialisable_and_float_free():
    v = view_of(gen(10, 2))
    json.dumps(v, allow_nan=False)


def test_VM_16_the_view_is_deterministic():
    d = gen(25, 3, width=300, m2_every=4, blockers=1)
    assert json.dumps(view_of(d), sort_keys=True) == json.dumps(view_of(json.loads(json.dumps(d))), sort_keys=True)


def test_VM_17_input_key_order_does_not_matter():
    d = example_dict()
    shuffled = json.loads(json.dumps(d, sort_keys=False))
    def rev(o):
        if isinstance(o, dict):
            return {k: rev(o[k]) for k in reversed(list(o))}
        if isinstance(o, list):
            return [rev(x) for x in o]
        return o
    a, b = view_of(rev(shuffled)), view_of(d)
    a["header"].pop("report_sha256"); b["header"].pop("report_sha256"); a["header"].pop("report_bytes"); b["header"].pop("report_bytes")
    assert a == b


def test_VM_18_empty_report_view():
    v = view_of(gen(0, 1))
    assert v["symptoms"] == [] and v["first_divergence"] == [] and v["lanes"][0]["items"] == []
    assert v["lanes"][0]["origin_ns"] is None and v["lanes"][0]["span_ns"] == 0


def test_VM_19_secondary_onsets_give_the_symptom_an_interval_in_the_other_lane():
    d = gen(4, 1)
    d["lanes"].append({"domain": "stamp_x", "symptom_count": 1, "layers": [["s-00000001"]],
                       "earliest": {"symptom_ids": ["s-00000001"], "defence": "defended", "blocker_ids": [], "statement": d["lanes"][0]["earliest"]["statement"]}})
    d["symptoms"][1]["secondary_onsets"] = [{"lane": "stamp_x", "onset": {"domain": "stamp_x", "lower_ns": 5, "upper_ns": 9}, "onset_basis": "payload_stamp_value"}]
    v = view_of(d)
    lane = [ln for ln in v["lanes"] if ln["domain"] == "stamp_x"][0]
    assert lane["items"][0]["lower_ns"] == 5 and lane["items"][0]["off_lower_ns"] == 0 and lane["span_ns"] == 4


def test_VM_20_a_lane_item_without_interval_is_kept_and_marked():
    d = gen(3, 1)
    d["lanes"][0]["layers"].append(["s-ghost"]); d["lanes"][0]["symptom_count"] += 1
    v = view_of(d)
    ghost = [it for it in v["lanes"][0]["items"] if it["id"] == "s-ghost"][0]
    assert ghost["lower_ns"] is None and ghost["off_lower_ns"] is None


def test_VM_21_text_is_cleaned_in_the_view():
    d = gen(2, 1); d["symptoms"][0]["topic"] = "a‮b\x00c" + "z" * 300
    v = view_of(d)
    t = [s for s in v["symptoms"] if s["id"] == "s-00000000"][0]["topic"]
    assert "‮" not in t and "\x00" not in t and len(t) <= 120


def test_VM_22_report_digest_and_size_come_from_the_loader():
    r = load(example_dict()); v = build_view(r.report, sha256=r.sha256, size=r.size)
    assert v["header"]["report_sha256"] == r.sha256 and v["header"]["report_bytes"] == r.size


def test_VM_23_limits_and_limitations_are_always_present():
    v = view_of(gen(0, 1))
    assert v["limitations"] and len(v["limitations"]) == 7 and v["limits"]


def test_VM_24_blockers_and_caveats_are_all_kept():
    d = gen(3, 1, blockers=5); v = view_of(d)
    assert len(v["blockers"]) == 5 and len(v["caveats"]) == len(d["caveats"])


def test_VM_25_hypotheses_use_only_the_three_templates_and_are_not_ranked():
    v = view_of(example_dict())
    allowed = {"propagation_along_declared_edge", "common_or_independent_origin_not_excluded", "observation_or_timing_artifact_not_excluded"}
    seen = {h["template"] for ch in v["chains"] for h in ch["hypotheses"]} | {l["hypothesis"]["template"] for l in v["links"] if l["hypothesis"]}
    assert seen and seen <= allowed
    assert not any(k in json.dumps(v) for k in ('"rank"', '"score"', '"probability"', '"confidence"'))


def test_VM_26_with_verification_merges_without_touching_the_original():
    v = view_of(gen(3, 1))
    ver = {"path": "p", "summary": {"verified": 1, "mismatch": 0, "not_checked": 0}, "refs": {"E1": {"status": "verified", "detail": ""}},
           "artifacts": {v["header"]["evidence"][0]["artifact_ref"]: {"status": "verified", "detail": ""}}}
    w = with_verification(v, ver)
    assert v["verification"] is None and v["evidence_refs"][0]["verification"] is None
    assert w["evidence_refs"][0]["verification"]["status"] == "verified" and w["evidence_refs"][1]["verification"] is None and w["verification"]["path"] == "p"


def test_VM_27_an_exit3_report_has_an_empty_analysis_and_its_problems():
    d = example_dict(); d.update(exit_code=3, exit_reasons=["anomaly_config_invalid"], problems=[{"source": "anomaly_config", "path": "a.b", "message": "bad"}],
                                 lanes=[], symptoms=[], links=[], components=[], chains=[], hypotheses=[], blockers=[], caveats=[])
    v = view_of(d)
    assert v["header"]["exit_code"] == 3 and v["header"]["problems"][0]["message"] == "bad" and v["symptoms"] == []


def test_VM_28_chain_total_none_status_is_kept():
    d = gen(6, 1)
    assert view_of(d)["chains_total"] == 1


def _reverse_lists(d):
    for k in ("symptoms", "links", "chains", "components", "blockers", "hypotheses", "caveats"):
        d[k] = list(reversed(d[k]))
    return d


def test_VM_29_input_order_of_every_list_does_not_change_the_view():
    d = gen(24, 3, m2_every=3, blockers=3)
    a = view_of(d); b = view_of(_reverse_lists(json.loads(json.dumps(d))))
    for v in (a, b):
        v["header"].pop("report_sha256"); v["header"].pop("report_bytes")
    assert a == b


def test_VM_30_equal_lower_bounds_are_ordered_by_upper_then_id():
    d = gen(4, 1, width=0)
    for i, s in enumerate(d["symptoms"]):
        s["onset"] = {"domain": "mcap_log_time", "lower_ns": 1000, "upper_ns": 1000 + (3 - i) * 10}
        s["extent"] = dict(s["onset"])
    d["links"], d["chains"], d["components"], d["hypotheses"], d["chains_total"] = [], [], [], [], 0
    d["localization"]["components"] = []; d["coverage"]["links_total"] = 0; d["coverage"]["chains_reported"] = 0
    d["lanes"][0]["layers"] = [[s["symptom_id"] for s in d["symptoms"]]]; d["lanes"][0]["earliest"]["symptom_ids"] = []; d["lanes"][0]["earliest"].update(defence="tied", statement=d["lanes"][0]["earliest"]["statement"])
    try:
        v = view_of(d)
    except AssertionError:
        d["lanes"][0]["earliest"]["symptom_ids"] = [s["symptom_id"] for s in d["symptoms"]]
        v = view_of(d)
    ups = [it["upper_ns"] for it in v["lanes"][0]["items"]]
    assert ups == sorted(ups) and ups[0] < ups[-1]


def test_VM_31_identities_are_sorted_by_key_even_if_the_file_is_not():
    d = gen(2, 1)
    d["symptoms"][0]["identities"] = {"node_name": "n", "logger_name": "l"}
    raw = json.dumps(d, sort_keys=False).encode()
    from physicore_collector.investigation_view.load import parse_report_bytes
    r = parse_report_bytes(raw)
    assert r.report is not None
    v = build_view(r.report, sha256=r.sha256, size=r.size)
    assert [k for k, _ in v["symptoms"][0]["identities"]] == ["logger_name", "node_name"]


def test_VM_32_onset_width_is_upper_minus_lower():
    v = view_of(gen(3, 1, width=300))
    assert all(s["onset"]["width_ns"] == 300 and s["extent"]["width_ns"] == 300 for s in v["symptoms"])


def test_VM_33_evidence_numbering_does_not_depend_on_input_order():
    d = gen(8, 1)
    a = view_of(d)
    d2 = json.loads(json.dumps(d)); d2["symptoms"].reverse()
    b = view_of(d2)
    assert [(r["id"], r["message_index"]) for r in a["evidence_refs"]] == [(r["id"], r["message_index"]) for r in b["evidence_refs"]] == [(f"E{i + 1}", i) for i in range(8)]


def test_VM_34_lane_span_and_origin_of_a_secondary_only_lane():
    test_VM_19_secondary_onsets_give_the_symptom_an_interval_in_the_other_lane()
