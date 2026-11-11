"""M4b: from a catalogue and an M3d report, the candidate mechanisms for each investigation unit.

An investigation unit is one M3d component, or one symptom that belongs to no component. Matching is by the symptom categories (and, if a system model is
given, the declared component roles) found in the unit. A hypothesis instance binds the entry's slots to real topics, and records where each binding came
from. A slot that cannot be bound makes the checks that need it "not runnable: binding missing"; nothing is guessed.

Every unit's competing set also holds the two fixed M3d members and an explicit "unresolved" member, and a "no applicable mechanism" member when no entry
matches. No instance is ranked or scored here (ranking is M4c). The result is deterministic and does not depend on the order of the inputs.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from physicore_collector.localization.vocabulary import HYPOTHESIS_SENTENCES, HYPOTHESIS_TEMPLATES

from .catalogue import Catalogue
from .model import SystemModel

INSTANCE_VERSION = 1
UNRESOLVED_SENTENCE = "The available evidence does not separate the candidate mechanisms, and it is not enough to choose among them."
NO_MECHANISM_SENTENCE = "No mechanism in the catalogue applies to the symptoms in this unit."
BINDING_MISSING = "not runnable: binding missing"
FIXED_MEMBERS = (
    {"member": "m3d_" + HYPOTHESIS_TEMPLATES[1], "sentence": HYPOTHESIS_SENTENCES[(HYPOTHESIS_TEMPLATES[1], "not_excluded")]},
    {"member": "m3d_" + HYPOTHESIS_TEMPLATES[2], "sentence": HYPOTHESIS_SENTENCES[(HYPOTHESIS_TEMPLATES[2], "not_excluded")]},
)


def _h(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def units_of(report: dict) -> list[dict]:
    """Components, then lone symptoms, each with its topics and categories. Sorted by unit id."""
    sym = {s["symptom_id"]: s for s in report["symptoms"]}
    in_comp: set[str] = set()
    out = []
    for c in report["components"]:
        ids = sorted(c["symptom_ids"])
        in_comp.update(ids)
        out.append(_unit(c["component_id"], "component", ids, sym))
    for sid in sorted(set(sym) - in_comp):
        out.append(_unit(sid, "symptom", [sid], sym))
    return sorted(out, key=lambda u: u["unit_id"])


def _unit(uid, kind, ids, sym) -> dict:
    ss = [sym[i] for i in ids if i in sym]
    return {"unit_id": uid, "unit_kind": kind, "symptom_ids": ids, "topics": sorted({s["node"] for s in ss}), "categories": sorted({s["category"] for s in ss})}


def _model_maps(model: SystemModel | None):
    if model is None:
        return {}, {}, {}, []
    c = model.content
    topic_comp: dict[str, set[str]] = {}
    comp_topics: dict[str, set[str]] = {}
    for b in c["bindings"]:
        topic_comp.setdefault(b["topic"], set()).add(b["component"])
        comp_topics.setdefault(b["component"], set()).add(b["topic"])
    roles = {x["id"]: x["role"] for x in c["components"]}
    return topic_comp, comp_topics, roles, c["edges"]


def _ends(ref: str, comp_topics) -> set[str]:
    return {ref} if ref.startswith("/") else set(comp_topics.get(ref, ()))


def _bindings(entry: dict, unit: dict, model: SystemModel | None, dep_edges=()) -> tuple[dict, dict]:
    topic_comp, comp_topics, _roles, medges = _model_maps(model)
    edges = [(e, "system_model") for e in medges] + [({"from": e["from"], "to": e["to"]}, "dependency_file") for e in dep_edges]
    utopics = set(unit["topics"])
    ucomps = {c for t in utopics for c in topic_comp.get(t, ())}
    up: dict[str, str] = {}
    down: dict[str, str] = {}
    for e, esrc in edges:
        f, t = _ends(e["from"], comp_topics), _ends(e["to"], comp_topics)
        if t & utopics:
            up.update({x: esrc for x in f if x not in up or esrc == "system_model"})
        if f & utopics:
            down.update({x: esrc for x in t if x not in down or esrc == "system_model"})
    sib = {t for c in ucomps for t in comp_topics.get(c, ())} - utopics
    binds, srcs = {}, {}
    for s in entry["slots"]:
        sel = s["selects"]
        if sel == "symptom_topics":
            got, src = utopics, "m3d_report"
        elif sel in ("declared_upstream_topics", "declared_downstream_topics"):
            m = up if sel == "declared_upstream_topics" else down
            got = set(m) - utopics
            src = "system_model" if any(m[x] == "system_model" for x in got) else "dependency_file"
        elif sel == "sibling_topics":
            got, src = sib, "system_model"
        else:
            role = sel.split(":", 1)[1]
            got = {b["topic"] for b in (model.content["bindings"] if model else ()) if b["role"] == role and b["component"] in ucomps}
            src = "system_model"
        binds[s["name"]] = sorted(got)
        srcs[s["name"]] = src if got else "not_bound"
    return binds, srcs


def matches(entry: dict, unit: dict, model: SystemModel | None) -> bool:
    if not set(entry["applies_to"]["symptom_categories"]) & set(unit["categories"]):
        return False
    want = entry["applies_to"]["component_roles"]
    if not want:
        return True
    topic_comp, _ct, roles, _e = _model_maps(model)
    have = {roles[c] for t in unit["topics"] for c in topic_comp.get(t, ()) if c in roles}
    return bool(set(want) & have)


def instantiate(entry: dict, unit: dict, model: SystemModel | None = None, dep_edges=()) -> dict:
    binds, srcs = _bindings(entry, unit, model, dep_edges)
    checks = []
    for k in entry["checks"]:
        ok = bool(binds.get(k["slot"]))
        checks.append({"check_id": k["id"], "kind": k["kind"], "slot": k["slot"], "constraint_kind": k["constraint_kind"], "description": k["description"],
                       "runnable": ok, "state": "to_be_evaluated" if ok else BINDING_MISSING})
    return {
        "instance_id": "h-" + _h(entry["id"], entry["version"], unit["unit_id"], binds),
        "entry_id": entry["id"], "entry_version": entry["version"], "family": entry["family"], "mechanism": entry["mechanism"],
        "supplied_by": entry["supplied_by"], "review_status": entry["review"]["status"], "unit_id": unit["unit_id"],
        "bindings": binds, "binding_sources": srcs, "slot_selects": {x["name"]: x["selects"] for x in entry["slots"]},
        "predictions": [{"id": q["id"], "text": q["text"], "rule": q["rule"]} for q in entry["predictions"]],
        "checks": checks, "evidence_requirements": list(entry["evidence_requirements"]), "rivals": list(entry["rivals"]),
    }


def build(catalogue: Catalogue, report: dict, model: SystemModel | None = None, dependency_edges=()) -> dict:
    """The competing sets for every unit of the report. Pure, deterministic; no ranking. `dependency_edges` are declared edges ({"from","to"} topics) from a
    dependency file; they bind the upstream and downstream slots when no system model is given."""
    out_units = []
    total = 0
    for u in units_of(report):
        inst = [instantiate(e, u, model, dependency_edges) for e in catalogue.entries if matches(e, u, model)]
        inst.sort(key=lambda i: (i["entry_id"], i["instance_id"]))
        total += len(inst)
        members = [dict(m) for m in FIXED_MEMBERS] + [{"member": "unresolved", "sentence": UNRESOLVED_SENTENCE}]
        if not inst:
            members.append({"member": "no_applicable_mechanism", "sentence": NO_MECHANISM_SENTENCE})
        covered = sorted(set(u["topics"]) | {t for i in inst for ts in i["bindings"].values() for t in ts})
        out_units.append({**u, "covered_topics": covered, "instances": inst, "always_present": members})
    return {"instance_version": INSTANCE_VERSION, "catalogue_digest": catalogue.digest, "catalogue_sources": list(catalogue.sources),
            "model_digest": None if model is None else model.digest, "units": out_units, "instance_total": total}


__all__ = ["build", "units_of", "instantiate", "matches", "FIXED_MEMBERS", "UNRESOLVED_SENTENCE", "NO_MECHANISM_SENTENCE", "BINDING_MISSING"]
