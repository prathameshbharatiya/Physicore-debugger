"""M4a: the declared system model set beside the facts of a recording, and the consistency checks between the two.

Declared facts and recording facts are never merged: the report keeps them in separate sections, each marked with where it came from.
A finding is reported, never repaired: the model is not changed to fit the recording, and the recording is not read as the model.
Every sentence is fixed text or cleaned data from the model; nothing here states a cause.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from physicore_collector.inventory import exit_code as inventory_exit_code, inspect_evidence

import hashlib

from .model import LIMITS, SCHEMA_VERSION, SystemModel, load_system_model, read_regular_file

REPORT_VERSION = "1.0"
MAX_FINDINGS = 200
KIND_ORDER = ("dependency_file_digest_mismatch", "dependency_file_not_supplied", "declared_topic_not_recorded", "type_mismatch", "constraint_cannot_be_evaluated",
              "intervention_outside_recording", "recorded_topic_not_declared")
SENTENCES = {
    "declared_topic_not_recorded": "A topic that the model uses is not in the recording.",
    "recorded_topic_not_declared": "A topic in the recording is not used anywhere in the model.",
    "type_mismatch": "The message type recorded for a topic differs from the type the model declares.",
    "constraint_cannot_be_evaluated": "A declared constraint cannot be evaluated against this recording.",
    "intervention_outside_recording": "A declared intervention lies outside the time window of the recording.",
    "dependency_file_digest_mismatch": "The dependency file supplied does not have the digest that the model names.",
    "dependency_file_not_supplied": "The model names a dependency file by digest, but none was supplied to check it.",
}
SEVERITY = {"recorded_topic_not_declared": "note", "dependency_file_not_supplied": "note"}       # every other kind is a warning
LIMITATIONS = (
    "Everything in the model section was declared by the people named in it. This tool cannot verify it and never infers or corrects it.",
    "The recording section lists only what the recording's own metadata states: topics, message types and message counts. Quality-of-service settings are not exposed.",
    "A finding means that the model and the recording differ. It does not say which of the two is right.",
    "Real-recording validation of the readers and decoder licensing clearance are still outstanding.",
)


def recording_facts(inv) -> dict:
    """Topics, types and counts from the inventory, merged by topic name (one topic may have several channels). Marked as coming from the recording."""
    by: dict[str, dict] = {}
    for t in inv.topics:
        e = by.setdefault(t.topic, {"topic": t.topic, "types": set(), "message_count": 0})
        if t.schema_name:
            e["types"].add(t.schema_name)
        e["message_count"] += t.message_count
    w = inv.timebase.recording_window
    return {"source": "from_recording", "inspection_state": inv.inspection.state,
            "topics": [{"topic": e["topic"], "types": sorted(e["types"]), "message_count": e["message_count"]} for e in sorted(by.values(), key=lambda x: x["topic"])],
            "window_ns": None if w is None else {"min_log_time_ns": w.min_log_time_ns, "max_log_time_ns": w.max_log_time_ns}}


def _topic_of(model: SystemModel) -> dict[tuple[str, str], dict]:
    return {(b["component"], b["role"]): b for b in model.content["bindings"]}


def _finding(kind: str, subject: str, **detail) -> dict:
    return {"kind": kind, "severity": SEVERITY.get(kind, "warning"), "subject": subject, "detail": detail, "message": SENTENCES[kind]}


def check_dependency(model: SystemModel, dependency_sha: str | None, supplied: bool) -> list[dict]:
    """The one check that needs no recording: the dependency file named by digest, against the file supplied (if any)."""
    want = model.content["dependency_file_sha256"]
    if want is None:
        return []
    if not supplied:
        return [_finding("dependency_file_not_supplied", want)]
    return [] if dependency_sha == want else [_finding("dependency_file_digest_mismatch", want, supplied=dependency_sha)]


def check_model(model: SystemModel, facts: dict, *, dependency_sha: str | None = None, dependency_supplied: bool = False) -> list[dict]:
    """Findings between the declared model and the recording facts. Pure and deterministic."""
    out: list[dict] = []
    rec = {t["topic"]: t for t in facts["topics"]}
    c = model.content

    def add(kind, subject, **detail):
        out.append(_finding(kind, subject, **detail))

    out += check_dependency(model, dependency_sha, dependency_supplied)
    used = model.topics()
    for t in sorted(used - set(rec)):
        add("declared_topic_not_recorded", t)
    for b in c["bindings"]:
        r = rec.get(b["topic"])
        if r is not None and b["type"] is not None and r["types"] and b["type"] not in r["types"]:
            add("type_mismatch", b["topic"], component=b["component"], role=b["role"], declared=b["type"], recorded=r["types"])
    bind = _topic_of(model)

    def reasons(ref, *, need_field):
        b = bind[(ref["component"], ref["role"])]
        rs = []
        if b["topic"] not in rec:
            rs.append("topic_not_recorded")
        if need_field and b["field"] is None:
            rs.append("field_not_declared")
        return rs

    for k in c["constraints"]:
        rs: list[str] = []
        if k["kind"] == "value_range":
            rs = reasons(k, need_field=True)
        elif k["kind"] == "rate":
            rs = reasons(k, need_field=False)
        elif k["kind"] == "deadline":
            rs = sorted(set(reasons(k["trigger"], need_field=True) + reasons(k["response"], need_field=True)))
        else:
            if any(not any(b["component"] == m for b in c["bindings"]) for m in k["members"]):
                rs = ["member_without_binding"]
            elif all(not any(b["topic"] in rec for b in c["bindings"] if b["component"] == m) for m in k["members"]):
                rs = ["topic_not_recorded"]
        for r in rs:
            add("constraint_cannot_be_evaluated", k["id"], constraint_kind=k["kind"], reason=r)
    w = facts["window_ns"]
    for i in c["interventions"]:
        if w is not None and not (w["min_log_time_ns"] <= i["at_ns"] <= w["max_log_time_ns"]):
            add("intervention_outside_recording", i["id"], at_ns=i["at_ns"], min_log_time_ns=w["min_log_time_ns"], max_log_time_ns=w["max_log_time_ns"])
    for t in sorted(set(rec) - used):
        add("recorded_topic_not_declared", t)
    out.sort(key=lambda f: (KIND_ORDER.index(f["kind"]), f["subject"], json.dumps(f["detail"], sort_keys=True)))
    return out


def counts(c: dict) -> dict:
    return {k: len(c[k]) for k in ("components", "bindings", "versions", "constraints", "interventions", "edges")}


def build_report(model: SystemModel, facts: dict | None, findings: list[dict] | None, *, exit_code: int = 0) -> dict:
    total = None if findings is None else len(findings)
    rep: dict[str, Any] = {
        "model_report_version": REPORT_VERSION, "exit_code": exit_code,
        "model": {"source": "declared", "schema_version": SCHEMA_VERSION, "file_sha256": model.file_sha256, "digest": model.digest, "counts": counts(model.content),
                  "content": model.content},
        "recording": facts if facts is not None else {"source": "not_supplied"},
        "findings": None if findings is None else findings[:MAX_FINDINGS], "findings_total": total,
        "findings_truncated": bool(findings is not None and len(findings) > MAX_FINDINGS),
        "limitations": list(LIMITATIONS)}
    return rep


def to_json(rep: dict) -> str:
    return json.dumps(rep, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render_text(rep: dict) -> str:
    m, rec = rep["model"], rep["recording"]
    c = m["content"]
    L = [f"System model: {c['system_name']}  (declared by {c['declared_by']})",
         f"  file sha256   {m['file_sha256']}", f"  model digest  {m['digest']}",
         "  declared: " + ", ".join(f"{n} {k}" for k, n in m["counts"].items())]
    if c["operating_context"]:
        L.append("  operating context (declared): " + " ".join(c["operating_context"].split()))
    L.append("")
    if rec["source"] == "not_supplied":
        L.append("Recording: not supplied, so the model was validated but not compared with any recording.")
    else:
        L.append(f"Recording facts (from the recording, state {rec['inspection_state']}): {len(rec['topics'])} topics")
        for t in rec["topics"]:
            L.append(f"  {t['topic']}  {', '.join(t['types']) or 'type not recorded'}  {t['message_count']} messages")
    L.append("")
    if rep["findings"] is not None:
        L.append(f"Findings: {rep['findings_total']}" + (f" (first {len(rep['findings'])} shown)" if rep["findings_truncated"] else ""))
        for f in rep["findings"]:
            det = " ".join(f"{k}={v}" for k, v in sorted(f["detail"].items()))
            L.append(f"  [{f['severity']}] {f['kind']}: {f['subject']}" + (f"  ({det})" if det else ""))
            L.append(f"      {f['message']}")
        L.append("")
    L.append("Limits:")
    L += [f"  - {s}" for s in rep["limitations"]]
    return "\n".join(L) + "\n"


def run_system_model(model_path: str | Path, evidence: str | Path | None = None, dependencies: str | Path | None = None) -> dict:
    """-> {'exit_code', 'problems', 'report'}. 0 = model valid; 1 = valid, the recording is partial or unverified; 2 = the recording cannot be inspected or the
    dependency file cannot be read; 3 = the model is invalid (nothing is compared)."""
    loaded = load_system_model(model_path)
    if loaded.model is None:
        lines = [f"{p.path}: {p.message}" for p in loaded.problems]
        if loaded.problems_total > len(loaded.problems):
            lines.append(f"... and {loaded.problems_total - len(loaded.problems)} more problems")
        return {"exit_code": 3, "problems": lines, "report": None}
    model = loaded.model
    dep_sha, supplied = None, False
    if dependencies is not None:
        data, why = read_regular_file(dependencies, LIMITS["file_bytes"])
        if data is None:
            return {"exit_code": 2, "problems": [f"dependency file: {why}"], "report": None}
        dep_sha, supplied = hashlib.sha256(data).hexdigest(), True
    if evidence is None:
        dep = check_dependency(model, dep_sha, supplied)
        return {"exit_code": 0, "problems": [], "report": build_report(model, None, dep)}
    inv = inspect_evidence(evidence)
    code = inventory_exit_code(inv)
    if code == 2:
        return {"exit_code": 2, "problems": ["the evidence could not be inspected"], "report": None}
    facts = recording_facts(inv)
    findings = check_model(model, facts, dependency_sha=dep_sha, dependency_supplied=supplied)
    return {"exit_code": code, "problems": [], "report": build_report(model, facts, findings, exit_code=code)}


__all__ = ["run_system_model", "check_model", "check_dependency", "recording_facts", "build_report", "render_text", "to_json"]
