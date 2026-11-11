"""M4c: the discriminating checks of hypothesis instances.

A check is answered in one of three ways, and the way is always recorded:
  - "m3d_report": the M3d run already evaluated an M2 contract and reported a symptom of that kind on a bound topic. That symptom is the observed
    evidence that the check is violated. Silence in the report is NOT read as "satisfied": the report does not say which expectations the contract held.
  - "generated_contract": a value-range expectation is compiled from a declared `value_range` constraint of the system model, written into a generated
    M2 contract (hashed and kept in the result), and run through the accepted investigator, but only if the recording's digests match those in the report.
  - otherwise the check is "not runnable", with the reason.
Outcomes: violated, satisfied, insufficient_evidence, incomparable_clock_domains, not_runnable, not_run. Nothing here states a cause.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .model import SystemModel

OUTCOMES = ("violated", "satisfied", "insufficient_evidence", "incomparable_clock_domains", "not_runnable", "not_run")
M2_SYMPTOM = {"range": "m2_range", "tolerance": "m2_tolerance", "equals": "m2_equals", "transition": "m2_transition", "event_response": "m2_event_response"}
REASONS = {
    "no_constraint": "The model declares no value-range constraint with a field for the bound topics.",
    "unsupported_kind": "No declared constraint of this kind can be turned into a check from the model.",
    "binding_missing": "The topics this check needs are not bound.",
    "no_symptom": "The M3d run reported no symptom of this kind on the bound topics, and the report does not say which expectations its contract held.",
    "digest_mismatch": "The recording's digests do not match those in the M3d report, so the generated contract was not run.",
    "no_recording": "No recording was supplied, so the generated contract was not run.",
}
CONTRACT_SOURCE = {"kind": "specification", "reference": "physicore M4c generated from a declared model constraint"}


def _h(x) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _m3d_symptoms(report: dict, kind: str, topics: list[str]) -> list[str]:
    cat = M2_SYMPTOM[kind]
    return sorted(s["symptom_id"] for s in report["symptoms"] if s["category"] == cat and s["node"] in set(topics))


def plan(built: dict, report: dict, model: SystemModel | None) -> dict:
    """-> {"checks": {key: state}, "contract": dict | None, "contract_sha256": str | None}. Pure; runs nothing."""
    exps: list[dict] = []
    checks: dict[str, dict] = {}
    for u in built["units"]:
        for i in u["instances"]:
            for k in i["checks"]:
                key = f"{i['instance_id']}/{k['check_id']}"
                topics = i["bindings"].get(k["slot"], [])
                st = {"instance_id": i["instance_id"], "check_id": k["check_id"], "kind": k["kind"], "topics": topics, "outcome": None, "reason": None, "source": None,
                      "symptom_ids": [], "expectation_ids": []}
                checks[key] = st
                if not topics:
                    st.update(outcome="not_runnable", reason="binding_missing", source="none")
                    continue
                syms = _m3d_symptoms(report, k["kind"], topics)
                if syms:
                    st.update(outcome="violated", source="m3d_report", symptom_ids=syms)
                    continue
                if k["kind"] == "range" and model is not None:
                    bind = {(b["component"], b["role"]): b for b in model.content["bindings"]}
                    for c in model.content["constraints"]:
                        b = bind.get((c.get("component"), c.get("role"))) if c["kind"] == "value_range" else None
                        if b is not None and b["topic"] in topics and b["field"] is not None:
                            check = {"kind": "range"}
                            if c["min"] is not None:
                                check["min"] = c["min"]
                            if c["max"] is not None:
                                check["max"] = c["max"]
                            eid = "G-" + _h([c["id"], b["topic"], b["field"]])[:12]
                            exps.append({"expectation_id": eid, "description": f"generated from declared constraint {c['id']}", "topic": b["topic"], "signal": b["field"],
                                         "clock_domain": "mcap_log_time", "check": check, "source": dict(CONTRACT_SOURCE)})
                            st["expectation_ids"].append(eid)
                    if st["expectation_ids"]:
                        st.update(source="generated_contract")
                        continue
                    st.update(outcome="not_runnable", reason="no_constraint", source="none")
                elif k["kind"] == "range":
                    st.update(outcome="not_runnable", reason="no_constraint", source="none")
                elif k["kind"] == "event_response":
                    st.update(outcome="insufficient_evidence", reason="no_symptom", source="m3d_report")
                else:
                    st.update(outcome="not_runnable", reason="unsupported_kind", source="none")
    uniq = {e["expectation_id"]: e for e in exps}
    contract = None
    if uniq:
        contract = {"contract_schema_version": "1.0", "contract_id": "m4c-generated", "expectations": [uniq[k] for k in sorted(uniq)]}
    return {"checks": checks, "contract": contract, "contract_sha256": None if contract is None else _h(contract)}


def digests_match(report: dict, inv) -> bool:
    want = sorted((e["artifact_ref"], e["sha256"]) for e in report["evidence"])
    have = sorted((a.ref_id, a.sha256) for a in inv.artifacts if a.kind == "mcap_recording" and a.sha256)
    return bool(want) and want == have


def _combine(statuses: list[str]) -> str:
    if "violated" in statuses:
        return "violated"
    if statuses and all(s == "satisfied" for s in statuses):
        return "satisfied"
    for s in ("incomparable_clock_domains", "insufficient_evidence", "not_runnable"):
        if s in statuses:
            return s
    return "insufficient_evidence"


def run(pl: dict, report: dict, evidence: str | Path | None) -> dict:
    """Fill in the outcomes of the generated checks. The contract runs only if the recording's digests match the report's. Read-only."""
    pending = [c for c in pl["checks"].values() if c["outcome"] is None]
    res: dict[str, str] = {}
    why = None
    if pending:
        if pl["contract"] is None:
            why = "no_recording"
        elif evidence is None:
            why = "no_recording"
        else:
            from physicore_collector.inventory import inspect_evidence
            from physicore_collector.investigator import investigate
            from physicore_collector.investigator.contract import parse_contract
            inv = inspect_evidence(evidence)
            if not digests_match(report, inv):
                why = "digest_mismatch"
            else:
                loaded = parse_contract(json.loads(json.dumps(pl["contract"])))
                r = investigate(inv, loaded)
                m = {"divergence_found": "violated", "no_divergence_observed_in_covered_window": "satisfied", "insufficient_evidence": "insufficient_evidence",
                     "incomparable_timebases": "incomparable_clock_domains", "invalid_expectation": "not_runnable"}
                res = {x.expectation_id: m[x.status.value] for x in r.expectation_results}
    out = {}
    for key, c in sorted(pl["checks"].items()):
        c = dict(c)
        if c["outcome"] is None:
            if why:
                c.update(outcome="not_run", reason=why)
            else:
                c.update(outcome=_combine([res.get(e, "not_runnable") for e in c["expectation_ids"]]), reason=None)
        out[key] = c
    return {"checks": out, "contract": pl["contract"], "contract_sha256": pl["contract_sha256"]}


__all__ = ["plan", "run", "digests_match", "OUTCOMES", "REASONS"]
