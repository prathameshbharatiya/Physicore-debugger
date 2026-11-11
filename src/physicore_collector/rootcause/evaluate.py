"""M4c: each hypothesis instance is held against the M3d report and the check outcomes. The result is an evidence ledger, an ordinal tier, and a verdict on
the competing set. Nothing here is a probability or a score, and no mechanism is called the cause.

Rules (fixed, stated in every result):
  - A prediction is satisfied only by something observed in the report (a symptom of the right category on a bound topic) or by a check outcome.
  - A prediction about the unit's own symptom topics only restates the symptom that made the entry match. It is neutral: it neither supports nor counts.
  - A symptom that is not reported is never read as proof that nothing happened: the report does not say which topics a detector covered. So an unmet
    "present" prediction stays open, and an "absent" prediction can only be contradicted (by a reported symptom), never satisfied.
  - Tiers: contradicted (some prediction is contradicted); not_assessed (nothing about the instance can be assessed from this evidence); supported (at
    least one prediction satisfied); possible (assessable, but nothing satisfied).
  - Order: no contradiction first, then the larger satisfied share, then fewer open predictions, then the entry id (only to make the order reproducible).
  - A set has a leader only if the first instance is supported, its satisfied share is at least 1/2, it exceeds every other non-contradicted instance by at
    least 1/3, and the unit has no blocker. Otherwise the set is competing, or unresolved when nothing is supported.
"""
from __future__ import annotations

import json
from fractions import Fraction
from typing import Any

from . import checks as K
from . import instantiate as I

EVALUATION_VERSION = 1
MARGIN = Fraction(1, 3)
MIN_SHARE = Fraction(1, 2)
STATES = ("satisfied", "contradicted", "not_observed", "absence_not_established", "binding_missing", "check_open", "not_evaluable", "restates_symptom")
ASSESSABLE = ("satisfied", "contradicted", "not_observed")
OPEN = ("binding_missing", "check_open", "not_evaluable")
RULE_TEXT = ("A set has a leader only if its first instance is supported, has a satisfied share of at least 1/2, exceeds every other instance that is not "
             "contradicted by at least 1/3, and its unit has no blocker. Otherwise the set is competing, or unresolved when nothing is supported.")
SET_SENTENCES = {
    "leading": "This mechanism leads the other candidates under the stated rule; the other candidates are not excluded.",
    "competing": "Several candidate mechanisms remain, and the evidence does not separate them under the stated rule.",
    "unresolved": I.UNRESOLVED_SENTENCE,
    "no_applicable_mechanism": I.NO_MECHANISM_SENTENCE,
}
LIMITATIONS = (
    "Tiers order candidates by how many of their predictions the evidence meets. They are not probabilities.",
    "A prediction that the evidence does not meet stays open; the report cannot show that nothing happened on a topic.",
    "Results on the synthetic corpus show that the software follows its definitions, not accuracy on real robots.",
    "Real-recording validation and decoder licensing clearance are still outstanding.",
)


def _fr(n: int, d: int) -> Fraction:
    return Fraction(n, d) if d else Fraction(0)


def _refs(sym: dict) -> list[dict]:
    return [{"artifact_ref": e.get("artifact_ref"), "topic": e.get("topic"), "message_index": e.get("message_index"), "time_ns": e.get("time_ns")}
            for e in sym.get("evidence", [])[:3]]


def _eval_instance(inst: dict, syms: list[dict], check_results: dict | None, links: list[dict], unit_syms: set[str]) -> dict:
    ledger: list[dict] = []
    preds: list[dict] = []

    def item(kind, stance, pid, **kw):
        ledger.append({"item_id": f"{inst['instance_id']}#{len(ledger) + 1}", "kind": kind, "stance": stance, "prediction_id": pid, **kw})

    for slot, src in sorted(inst["binding_sources"].items()):
        if src in ("system_model", "dependency_file"):
            item("assumption", "neutral", None, source=src, slot=slot, topics=inst["bindings"][slot])
    for q in inst["predictions"]:
        r, pid = q["rule"], q["id"]
        state, why = None, None
        if r["op"] == "not_evaluable":
            state = "not_evaluable"
        elif r["op"] in ("symptom_present", "symptom_absent"):
            topics = inst["bindings"].get(r["slot"], [])
            if not topics:
                state = "binding_missing"
            elif inst["slot_selects"][r["slot"]] == "symptom_topics":
                state = "restates_symptom"                  # the symptom that made the entry match cannot also be evidence for it
            else:
                hit = [s for s in syms if s["node"] in topics and s["category"] in r["categories"]]
                sel = inst["slot_selects"][r["slot"]]
                if r["op"] == "symptom_present" and sel in ("declared_downstream_topics", "declared_upstream_topics") and hit:
                    # a symptom on a declared neighbour counts only through the M3d link between it and a symptom of this unit
                    down = sel == "declared_downstream_topics"
                    def _rel(s):
                        if down:
                            return [lk for lk in links if lk["from_symptom"] in unit_syms and lk["to_symptom"] == s["symptom_id"]]
                        return [lk for lk in links if lk["to_symptom"] in unit_syms and lk["from_symptom"] == s["symptom_id"]]
                    related = {s["symptom_id"]: _rel(s) for s in hit}
                    good = [s for s in hit if any(lk["label"] == "consistent_with_propagation" for lk in related[s["symptom_id"]])]
                    bad = [s for s in hit if s not in good and any(lk["label"] in ("inconsistent_order", "outside_declared_window") for lk in related[s["symptom_id"]])]
                    for s in bad:
                        for lk in related[s["symptom_id"]]:
                            item("inferred", "contradicts", pid, rule="m3d_link_label", link_id=lk["link_id"], label=lk["label"], symptom_id=s["symptom_id"])
                    state = "satisfied" if good else ("contradicted" if bad else "not_observed")
                    hit = good
                    for s in hit:
                        lid = sorted(lk["link_id"] for lk in related[s["symptom_id"]] if lk["label"] == "consistent_with_propagation")[0]
                        item("observed", "supports", pid, symptom_id=s["symptom_id"], category=s["category"], topic=s["node"], refs=_refs(s), link_id=lid)
                    preds.append({"prediction_id": pid, "state": state, "text": q["text"]})
                    continue
                for s in hit:
                    item("observed", "supports" if r["op"] == "symptom_present" else "contradicts", pid, symptom_id=s["symptom_id"], category=s["category"],
                         topic=s["node"], refs=_refs(s))
                if r["op"] == "symptom_present":
                    state = "satisfied" if hit else "not_observed"
                else:
                    state = "contradicted" if hit else "absence_not_established"
        else:                                                                   # check_violated
            c = (check_results or {}).get("checks", {}).get(f"{inst['instance_id']}/{r['check']}")
            if c is None or c["outcome"] in ("not_runnable", "not_run", "insufficient_evidence", "incomparable_clock_domains") or c["outcome"] is None:
                state = "binding_missing" if (c is not None and c["reason"] == "binding_missing") else "check_open"
                item("unconfirmed", "missing", pid, check_id=r["check"], outcome=None if c is None else c["outcome"], reason=None if c is None else c["reason"])
            elif c["outcome"] == "violated":
                state = "satisfied"
                if c["source"] == "m3d_report":
                    for sid in c["symptom_ids"]:
                        s = next((x for x in syms if x["symptom_id"] == sid), None)
                        item("observed", "supports", pid, symptom_id=sid, category=None if s is None else s["category"], topic=None if s is None else s["node"],
                             refs=[] if s is None else _refs(s))
                else:
                    item("inferred", "supports", pid, rule="generated_contract_check", check_id=r["check"], expectation_ids=c["expectation_ids"])
            else:                                                               # satisfied: the declared constraint held in the covered window
                state = "contradicted"
                item("inferred", "contradicts", pid, rule="generated_contract_check", check_id=r["check"], expectation_ids=c["expectation_ids"])
        preds.append({"prediction_id": pid, "state": state, "text": q["text"]})
    for rq in inst["evidence_requirements"]:
        if rq["usual_availability"] == "usually_absent":
            item("unconfirmed", "missing", None, token=rq["token"])
    direct = [p for p in preds if p["state"] not in ("not_evaluable", "restates_symptom")]
    sat = sum(1 for p in preds if p["state"] == "satisfied")
    contra = sum(1 for p in preds if p["state"] == "contradicted")
    open_n = sum(1 for p in preds if p["state"] in OPEN)
    assessable = sum(1 for p in preds if p["state"] in ASSESSABLE)
    if contra:
        tier, status = "contradicted", "assessed"
    elif not assessable:
        tier, status = "not_assessed", "cannot_be_assessed"
    elif sat:
        tier, status = "supported", "assessed"
    else:
        tier, status = "possible", "assessed"
    return {"instance_id": inst["instance_id"], "entry_id": inst["entry_id"], "family": inst["family"], "mechanism": inst["mechanism"], "tier": tier, "status": status,
            "components": {"contradicted": contra, "satisfied": sat, "direct": len(direct), "open": open_n},
            "satisfied_share": f"{sat}/{len(direct)}" if direct else "0/0", "review_status": inst["review_status"], "supplied_by": inst["supplied_by"],
            "bindings": inst["bindings"], "binding_sources": inst["binding_sources"], "predictions": preds, "ledger": ledger,
            "_share": _fr(sat, len(direct)), "_open": open_n}


def _order(evs: list[dict]) -> list[dict]:
    return sorted(evs, key=lambda e: (e["tier"] == "contradicted", -e["_share"], e["_open"], e["entry_id"], e["instance_id"]))


def _verdict(unit: dict, ordered: list[dict], blocked: bool) -> dict:
    if not ordered:
        return {"set_status": "no_applicable_mechanism", "leader": None, "reason": "no_entry_applies"}
    live = [e for e in ordered if e["tier"] != "contradicted"]
    supported = [e for e in live if e["tier"] == "supported"]
    if not supported:
        return {"set_status": "unresolved", "leader": None, "reason": "nothing_supported"}
    top = live[0]
    if blocked:
        return {"set_status": "competing", "leader": None, "reason": "blocker_present"}
    if top["tier"] != "supported" or top["_share"] < MIN_SHARE:
        return {"set_status": "competing", "leader": None, "reason": "share_below_minimum"}
    if any(top["_share"] - o["_share"] < MARGIN for o in live[1:]):
        return {"set_status": "competing", "leader": None, "reason": "margin_not_met"}
    return {"set_status": "leading", "leader": top["instance_id"], "reason": "rule_met"}


def evaluate(built: dict, report: dict, check_results: dict | None = None) -> dict:
    """Evaluate every unit of an instantiated catalogue against the report. Pure, deterministic."""
    syms = report["symptoms"]
    comps = {c["component_id"]: c for c in report["components"]}
    out_units = []
    for u in built["units"]:
        evs = [_eval_instance(i, syms, check_results, report["links"], set(u["symptom_ids"])) for i in u["instances"]]
        ordered = _order(evs)
        blocked = u["unit_kind"] == "component" and bool(comps.get(u["unit_id"], {}).get("blocker_ids"))
        v = _verdict(u, ordered, blocked)
        live = [e for e in ordered if e["tier"] != "contradicted"]
        missing = sorted({it["token"] for e in live for it in e["ledger"] if it["kind"] == "unconfirmed" and "token" in it})
        assumptions = [{"instance_id": e["instance_id"], "entry_id": e["entry_id"], "source": it["source"], "slot": it["slot"], "topics": it["topics"]}
                       for e in ordered for it in e["ledger"] if it["kind"] == "assumption"]
        clean = [{k: v_ for k, v_ in e.items() if not k.startswith("_")} for e in ordered]
        for n, e in enumerate(clean, 1):
            e["order"] = n
        out_units.append({k: u[k] for k in ("unit_id", "unit_kind", "symptom_ids", "topics", "categories", "covered_topics")} |
                         {"instances": clean, **v, "set_sentence": SET_SENTENCES[v["set_status"]], "always_present": u["always_present"], "missing_evidence": missing,
                          "assumptions": assumptions})
    return {"evaluation_version": EVALUATION_VERSION, "rule": RULE_TEXT, "catalogue_digest": built["catalogue_digest"], "model_digest": built["model_digest"],
            "units": out_units, "limitations": list(LIMITATIONS)}


def _signature(ev: dict) -> dict:
    return {u["unit_id"]: (u["set_status"], next((i["entry_id"] for i in u["instances"] if i["instance_id"] == u["leader"]), None)) for u in ev["units"]}


def run_all(catalogue, report: dict, model=None, dependency_edges=(), evidence=None) -> dict:
    """Instantiate, plan and run the checks, evaluate, then repeat without each declared source of bindings to see whether the verdicts depend on it."""
    def once(m, edges, ev_path):
        b = I.build(catalogue, report, m, edges)
        pl = K.plan(b, report, m)
        res = K.run(pl, report, ev_path)
        return b, res, evaluate(b, report, res)
    b, res, base = once(model, list(dependency_edges), evidence)
    sens = []
    for src, m, edges in (("dependency_file", model, []), ("system_model", None, list(dependency_edges))):
        if (src == "dependency_file" and not dependency_edges) or (src == "system_model" and model is None):
            continue
        _b, _r, alt = once(m, edges, evidence)
        sa, sb = _signature(base), _signature(alt)
        sens.append({"without": src, "robust": sa == sb, "changed_units": sorted(k for k in sa if sa[k] != sb.get(k))})
    base["sensitivity"] = sens
    base["checks"] = [{k: v for k, v in c.items()} for c in res["checks"].values()]
    base["generated_contract"] = res["contract"]
    base["generated_contract_sha256"] = res["contract_sha256"]
    return base


def to_score_input(truth_scenario_id: str, ev: dict) -> dict:
    """The harness input of M4-0, filled from a real evaluation. One set per unit; the explicit unresolved member is always present."""
    sets = []
    for u in ev["units"]:
        hy = [{"class": i["entry_id"], "tier": ("leading" if i["instance_id"] == u["leader"] else i["tier"]), "status": i["status"],
               "source_topic": None} for i in u["instances"]
              if not i["entry_id"].startswith("user.")]
        hy.append({"class": "unresolved", "tier": "possible", "status": "unresolved", "source_topic": None})
        sets.append({"set_id": u["unit_id"], "topics": u["covered_topics"], "hypotheses": hy})
    missing = sorted({t for u in ev["units"] for t in u["missing_evidence"]})
    sentences = [{"text": u["set_sentence"], "claim_level": 1} for u in ev["units"]]
    return {"score_input_version": 1, "scenario_id": truth_scenario_id, "sets": sets, "named_missing_evidence": missing, "sentences": sentences}


def to_json(ev: dict) -> str:
    return json.dumps(ev, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


__all__ = ["evaluate", "run_all", "to_score_input", "to_json", "RULE_TEXT", "MARGIN", "MIN_SHARE"]
