"""M4d: the claim ladder and the causal layer.

Every hypothesis instance gets exactly one claim level, 0 to 4, computed from the evidence in its ledger. The words that may be used about it come from a
fixed table keyed by level; no code path attaches another word. Level 5 (a controlled test on the system) is not reachable here and nothing here writes it.

A level is the highest one whose evidence is fully present; missing evidence never raises it. A blocker, an open or not runnable check, or a direct
contradiction caps the level, and the cap and its reason are reported. Nothing here states a cause.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from physicore_collector.localization.vocabulary import find_forbidden

from . import evaluate as E

CAUSAL_VERSION = 1
MAX_REACHABLE = 4
LEVELS = (
    (0, "observed_order", "The symptoms of this unit were observed in the recording in the order listed."),
    (1, "consistent_with_declared_dependency", "The order fits a declared dependency edge and window, which is consistent with propagation along the declared edge."),
    (2, "compatible_with_mechanism", "The evidence is compatible with this mechanism; it is supported by the listed evidence."),
    (3, "alternatives_excluded_within_coverage", "The recorded evidence favours this mechanism over the listed alternatives."),
    (4, "difference_evidence", "The evidence indicates this mechanism, supported by a contrast between these recordings."),
)
LEVEL_NAME = {n: name for n, name, _ in LEVELS}
LEVEL_SENTENCE = {n: s for n, _, s in LEVELS}
RELEASED = {1: {"propagation"}}                      # a word the screen forbids elsewhere, released from this level upward (see the harness)
RAISE = {
    0: "A declared dependency edge and window in which the order of the symptoms fits would raise this to level 1.",
    1: "Direct checks of this mechanism's predictions on the recording, with none left open, would raise this to level 2.",
    2: "Direct evidence that contradicts each listed alternative would raise this to level 3.",
    3: "A contrast under comparable conditions, such as a healthy baseline recording or a declared intervention, would raise this to level 4.",
    4: "Only a deliberate, controlled test on the system, which this tool does not run, could raise this to level 5.",
}
CAP_SENTENCES = {
    "blocker_present": "A blocker reported by the localization step could hide a relevant symptom, so the level is capped at 1.",
    "check_not_run": "A check of this mechanism could not be run or answered, so the level is capped at 1.",
    "contradicted": "A prediction of this mechanism is contradicted by the evidence listed beside it, so the level is capped at 1.",
    "baseline_not_comparable": "The baseline report is not comparable with this one, so level 4 is not available.",
}
REQUEST_MEANING = "If this measurement is available and shows what the mechanism predicts, its level can rise; if it shows the opposite, the mechanism is contradicted."
READINGS = {
    "upstream_covered_without_symptom": "The upstream endpoint was covered by a detector and shows no symptom, while the downstream endpoint does.",
    "endpoints_have_symptoms": "Both endpoints of the declared edge show symptoms.",
    "upstream_not_covered": "The upstream endpoint was not covered by a detector, so its silence says nothing.",
    "downstream_without_symptom": "The downstream endpoint shows no symptom.",
}
# words of a stronger level that a sentence at a lower level must not carry (checked in addition to the screen of the localization step)
STRONGER = {2: ("compatible with this mechanism",), 3: ("favours",), 4: ("indicates this mechanism",), 5: ("confirmed",)}


def gate(text: str, level: int) -> list[str]:
    """Words in `text` that its claim level does not allow. Empty means the sentence is allowed at that level."""
    bad = [w for w in find_forbidden(text) if not any(w.lower() in {x.lower() for x in words} for lvl, words in RELEASED.items() if level >= lvl)]
    low = text.lower()
    for lvl, words in STRONGER.items():
        if lvl > level:
            bad += [w for w in words if w in low]
    return bad


def comparable(a: dict, b: dict) -> dict:
    """Two reports are comparable only if detector configuration, dependency file and parameters agree. Names every item that differs."""
    ia, ib = a["inputs"], b["inputs"]
    diff = []
    if {k: v for k, v in ia.items() if k != "dependencies"} != {k: v for k, v in ib.items() if k != "dependencies"}:      # the detector configuration digest
        diff.append("detector_configuration_digest")
    if (ia.get("dependencies") or {}).get("sha256") != (ib.get("dependencies") or {}).get("sha256"):
        diff.append("dependency_file_digest")
    if a.get("limits") != b.get("limits"):
        diff.append("parameters")
    return {"comparable": not diff, "differs": diff}


def _observed_pairs(inst: dict) -> set[tuple[str, str]]:
    return {(x["topic"], x["category"]) for x in inst["ledger"] if x["kind"] == "observed" and x.get("topic") and x.get("category")}


def baseline_contrast(inst: dict, base: dict) -> bool:
    """True if every (topic, category) that supports the instance is absent from the baseline report, and there is at least one."""
    pairs = _observed_pairs(inst)
    have = {(s["node"], s["category"]) for s in base["symptoms"]}
    return bool(pairs) and not (pairs & have)


def intervention_contrast(inst: dict, report: dict, model) -> dict | None:
    """A declared intervention with supporting symptoms only after it, and none of the same kind and topic before it. A natural experiment, never a controlled test."""
    if model is None:
        return None
    pairs = _observed_pairs(inst)
    times = {}
    for s in report["symptoms"]:
        t = [e["time_ns"] for e in s.get("evidence", []) if e.get("time_ns") is not None]
        if t:
            times.setdefault((s["node"], s["category"]), []).append(min(t))
    # A contrast is only available when every supporting topic/category has a
    # timestamp. Ignoring an untimed symptom would silently turn incomplete
    # temporal evidence into an apparent before/after comparison.
    pair_times = [times.get(pair, []) for pair in sorted(pairs)]
    if not pair_times or any(not values for values in pair_times):
        return None
    for iv in sorted(model.content["interventions"], key=lambda x: x["id"]):
        if all(t > iv["at_ns"] for values in pair_times for t in values):
            return {"intervention_id": iv["id"], "at_ns": iv["at_ns"]}
    return None


def propagation_analysis(report: dict, unit: dict) -> list[dict]:
    """Facts about the declared edges that touch the unit's topics, read from the localization step's edge coverage. Reported only; no rule here changes a tier."""
    out = []
    top = set(unit["topics"])
    for e in report.get("coverage", {}).get("edge_coverage", []):
        if e["from_node"] not in top and e["to_node"] not in top:
            continue
        if e["from_coverage"] == "covered" and e["from_symptom_count"] == 0 and e["to_symptom_count"] > 0:
            r = "upstream_covered_without_symptom"
        elif e["from_symptom_count"] > 0 and e["to_symptom_count"] > 0:
            r = "endpoints_have_symptoms"
        elif e["from_coverage"] != "covered":
            r = "upstream_not_covered"
        else:
            r = "downstream_without_symptom"
        out.append({"edge_id": e["edge_id"], "from_node": e["from_node"], "to_node": e["to_node"], "from_coverage": e["from_coverage"], "to_coverage": e["to_coverage"],
                    "from_symptom_count": e["from_symptom_count"], "to_symptom_count": e["to_symptom_count"], "reading": r, "sentence": READINGS[r]})
    return sorted(out, key=lambda x: x["edge_id"])


def _level(inst: dict, unit_ev: dict, by_entry: dict, comps: dict, report: dict, model, baseline: dict | None, cmp_: dict | None) -> dict:
    caps: list[str] = []
    raw = 0
    if any(x["kind"] == "observed" and x.get("link_id") for x in inst["ledger"]):
        raw = 1
    check_open = any(x["kind"] == "unconfirmed" and "check_id" in x for x in inst["ledger"])
    comp = comps.get(unit_ev["unit_id"], {}) if unit_ev["unit_kind"] == "component" else {}
    sat, direct = inst["components"]["satisfied"], inst["components"]["direct"]
    unmet = any(p["state"] == "not_observed" for p in inst["predictions"])
    if inst["tier"] == "supported" and sat and not unmet and E._fr(sat, direct) >= E.MIN_SHARE:
        raw = max(raw, 2)
        rivals = by_entry[inst["entry_id"]]
        peers = {i["entry_id"]: i for i in unit_ev["instances"]}
        if rivals and all(r in peers and peers[r]["tier"] == "contradicted" for r in rivals):
            raw = 3
            if baseline is not None and cmp_ and cmp_["comparable"] and baseline_contrast(inst, baseline):
                raw = 4
            elif intervention_contrast(inst, report, model) is not None:
                raw = 4
    if inst["tier"] == "contradicted":
        caps.append("contradicted")
    if comp.get("blocker_ids"):
        caps.append("blocker_present")
    if check_open:
        caps.append("check_not_run")
    level = raw if not caps else min(raw, 1)
    note = None
    if raw >= 3 and baseline is not None and cmp_ is not None and not cmp_["comparable"]:
        note = "baseline_not_comparable"
    return {"claim_level": level, "claim_name": LEVEL_NAME[level], "claim_sentence": LEVEL_SENTENCE[level], "uncapped_level": raw, "caps": sorted(set(caps)),
            "cap_sentences": [CAP_SENTENCES[c] for c in sorted(set(caps))] + ([CAP_SENTENCES[note]] if note else []), "would_raise": RAISE[level],
            "contradictions": [x["item_id"] for x in inst["ledger"] if x["stance"] == "contradicts"], "baseline_note": note}


def assign(ev: dict, built: dict, report: dict, model=None, baseline: dict | None = None) -> dict:
    """Add a claim level, its fixed sentence, caps, propagation facts and diagnostic asks to an evaluation. Returns a new dict."""
    out = copy.deepcopy(ev)
    cat_rivals = {}
    reqs_by_inst = {i["instance_id"]: i for u in built["units"] for i in u["instances"]}
    for u in built["units"]:
        for i in u["instances"]:
            cat_rivals[i["entry_id"]] = list(i["rivals"])
    comps = {c["component_id"]: c for c in report["components"]}
    cmp_ = None if baseline is None else comparable(report, baseline)
    for u in out["units"]:
        for i in u["instances"]:
            lv = _level(i, u, cat_rivals, comps, report, model, baseline, cmp_)
            i.update(lv)
            i["contrast"] = None
            if lv["claim_level"] == 4:
                i["contrast"] = ({"kind": "baseline"} if baseline is not None and cmp_["comparable"] and baseline_contrast(i, baseline)
                                 else {"kind": "declared_intervention", **(intervention_contrast(i, report, model) or {})})
        u["propagation_facts"] = propagation_analysis(report, u)
        reqs = []
        if u["set_status"] != "leading":
            for i in u["instances"]:
                if i["tier"] == "contradicted":
                    continue
                src = reqs_by_inst[i["instance_id"]]
                for rq in src["evidence_requirements"]:
                    if rq["usual_availability"] == "usually_absent":
                        reqs.append({"entry_id": i["entry_id"], "measurement": rq["token"], "description": rq["description"], "would_discriminate_from": sorted(src["rivals"]),
                                     "meaning": REQUEST_MEANING})
        u["diagnostic_asks"] = sorted(reqs, key=lambda r: (r["measurement"], r["entry_id"]))
    out["causal_version"] = CAUSAL_VERSION
    out["ladder"] = [{"level": n, "name": name, "wording": s, "reachable": n <= MAX_REACHABLE} for n, name, s in LEVELS] + [
        {"level": 5, "name": "controlled_intervention", "wording": None, "reachable": False}]
    out["baseline"] = None if cmp_ is None else cmp_
    return out


def sentences(ev: dict) -> list[dict]:
    """Every sentence of a result with its claim level, for the harness. A hypothesis' sentence is checked against its own level."""
    out = []
    for u in ev["units"]:
        out.append({"text": u["set_sentence"], "claim_level": 0})
        for i in u["instances"]:
            out.append({"text": i["claim_sentence"], "claim_level": i["claim_level"]})
            out.append({"text": i["would_raise"], "claim_level": i["claim_level"]})
            for c in i["cap_sentences"]:
                out.append({"text": c, "claim_level": i["claim_level"]})
    return out


def to_score_input(scenario_id: str, ev: dict) -> dict:
    si = E.to_score_input(scenario_id, ev)
    si["sentences"] = sentences(ev)
    return si


__all__ = ["assign", "gate", "comparable", "baseline_contrast", "intervention_contrast", "propagation_analysis", "sentences", "to_score_input", "LEVELS", "RELEASED"]
