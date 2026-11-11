"""M4e: one investigation run. Inputs are a localization report (M3d), optionally a system model, the dependency file the report was made with, the recording,
a baseline report and a user catalogue file. The result is a strictly typed JSON report (schema "1.0") that the Markdown and HTML views are rendered from.

Nothing is written anywhere and nothing is repaired: an input that does not fit makes the run refuse (exit 3) rather than guess.
Exit codes: 0 = done; 1 = done, but a verification found a mismatch; 2 = an input could not be read (or a usage error); 3 = an input is invalid or inconsistent.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from physicore_collector.investigation_view.load import read_report
from physicore_collector.investigation_view.safety import clean_text
from physicore_collector.localization.dependencies import load_dependencies
from physicore_collector.localization.report import find_forbidden_keys
from physicore_collector.localization.vocabulary import find_forbidden

from . import catalogue as C
from . import causal as K
from . import evaluate as E
from .model import load_system_model

REPORT_VERSION = "1.0"
TOP_KEYS = ("investigation_report_version", "exit_code", "inputs", "summary", "units", "ladder", "rule", "sensitivity", "checks", "generated_contract_sha256",
            "verification", "problems", "limitations", "wording")
LIMITATIONS = E.LIMITATIONS + (
    "The catalogue entries are proposals until a named reviewer has signed each one off; the review status of every entry is shown.",
    "The level printed next to a hypothesis is the highest level whose evidence is fully present. It is not a measure of how likely the mechanism is.",
)
SETS = ("leading", "competing", "unresolved", "no_applicable_mechanism")
TIERS = ("contradicted", "not_assessed", "supported", "possible")


class InvestigationError(Exception):
    def __init__(self, code: int, problems: list[str]):
        self.code, self.problems = code, problems


def _loc(path, what):
    loaded = read_report(path)
    if loaded.report is None:
        raise InvestigationError(2 if loaded.unreadable else 3, [f"{what}: {clean_text(p, 300)}" for p in loaded.problems])
    return loaded


def dependency_edges(graph) -> list[dict]:
    """Declared edges as topic pairs: a node with bindings stands for its topics; a node that is itself a topic stands for itself."""
    topics: dict[str, set[str]] = {}
    for b in graph.bindings:
        topics.setdefault(b.node, set()).add(b.topic)
    out = []
    for e in graph.edges:
        for a in sorted(topics.get(e.src, {e.src} if e.src.startswith("/") else set())):
            for b in sorted(topics.get(e.dst, {e.dst} if e.dst.startswith("/") else set())):
                out.append({"from": a, "to": b})
    return out


def investigate(report_path, *, model_path=None, dependencies_path=None, evidence_path=None, baseline_path=None, catalogue_path=None, verify: bool = False) -> dict:
    """-> the investigation report (dict). Raises InvestigationError for exit codes 2 and 3."""
    loaded = _loc(report_path, "report")
    rep = json.loads(loaded.report.model_dump_json())
    problems: list[str] = []

    model = None
    if model_path is not None:
        lm = load_system_model(model_path)
        if lm.model is None:
            raise InvestigationError(3, [f"model: {p.path}: {p.message}" for p in lm.problems])
        model = lm.model

    edges: list[dict] = []
    want = (rep["inputs"].get("dependencies") or {}).get("sha256")
    if dependencies_path is not None:
        ld = load_dependencies(dependencies_path)
        if ld.graph is None:
            raise InvestigationError(3, [f"dependencies: {p.path}: {p.message}" for p in ld.problems])
        if want is None:
            raise InvestigationError(3, ["dependencies: the report was made without a dependency file, so the file given cannot belong to it"])
        if ld.sha256 != want:
            raise InvestigationError(3, ["dependencies: the dependency file given does not have the digest recorded in the report"])
        edges = dependency_edges(ld.graph)
    elif want is not None:
        problems.append("The report was made with a dependency file that was not supplied here, so declared neighbours are bound from the system model only.")

    baseline = None
    if baseline_path is not None:
        baseline = json.loads(_loc(baseline_path, "baseline").report.model_dump_json())

    cat = C.builtin()
    if catalogue_path is not None:
        uc = C.load_catalogue_file(catalogue_path)
        if uc.entries is None:
            raise InvestigationError(3, [f"catalogue: {p.path}: {p.message}" for p in uc.problems])
        try:
            cat = C.merged(uc)
        except ValueError as e:
            raise InvestigationError(3, [f"catalogue: {clean_text(str(e), 300)}"])

    if evidence_path is not None and not Path(evidence_path).exists():
        raise InvestigationError(2, ["evidence: the path does not exist"])
    if verify and evidence_path is None:
        raise InvestigationError(2, ["verification needs the recording: give --evidence"])

    ev = E.run_all(cat, rep, model, edges, evidence_path)
    built_units = _built(cat, rep, model, edges)
    out = K.assign(ev, built_units, rep, model, baseline)

    ver, code = None, 0
    if verify:
        from physicore_collector.investigation_view.model import build_view
        from physicore_collector.investigation_view.verify import verify_view
        r = verify_view(build_view(loaded.report, sha256=loaded.sha256, size=loaded.size), evidence_path)
        bad = sorted([k for k, v in {**r["refs"], **r["artifacts"]}.items() if v["status"] == "mismatch"])
        ver = {"summary": r["summary"], "mismatches": bad}
        code = 1 if r["summary"]["mismatch"] else 0
    checks_not_run = sorted({c["reason"] for c in out["checks"] if c["outcome"] == "not_run" and c["reason"]})
    if "digest_mismatch" in checks_not_run:
        problems.append("The recording's digests do not match the report's, so the generated contract was not run.")
        code = max(code, 1)

    counts = {"units": len(out["units"]), "instances": sum(len(u["instances"]) for u in out["units"]),
              "leaders": sum(1 for u in out["units"] if u["leader"]),
              "set_status": {k: sum(1 for u in out["units"] if u["set_status"] == k) for k in SETS},
              "claim_levels": {str(n): sum(1 for u in out["units"] for i in u["instances"] if i["claim_level"] == n) for n in range(0, 5)}}
    entries = {e["id"]: e for e in cat.entries}
    used = sorted({i["entry_id"] for u in out["units"] for i in u["instances"]})
    review = {k: sum(1 for x in used if entries[x]["review"]["status"] == k) for k in ("proposed", "reviewed", "user_supplied")}
    report = {
        "investigation_report_version": REPORT_VERSION, "exit_code": code,
        "inputs": {"localization_report": {"sha256": loaded.sha256, "size": loaded.size, "schema": rep["localization_schema_version"]},
                   "model": None if model is None else {"file_sha256": model.file_sha256, "digest": model.digest},
                   "dependencies": None if want is None else {"sha256": want, "supplied": dependencies_path is not None},
                   "evidence": {"supplied": evidence_path is not None}, "baseline": out["baseline"],
                   "catalogue": {"digest": cat.digest, "sources": list(cat.sources), "entries_used_by_review_status": review}},
        "summary": counts, "units": out["units"], "ladder": out["ladder"], "rule": out["rule"], "sensitivity": out["sensitivity"], "checks": out["checks"],
        "generated_contract_sha256": out["generated_contract_sha256"], "verification": ver, "problems": problems, "limitations": list(LIMITATIONS),
        "wording": {"released": {str(k): sorted(v) for k, v in sorted(K.RELEASED.items())}},
    }
    errs = validate_report(report)
    if errs:
        raise InvestigationError(3, ["internal: the report did not pass its own schema: " + "; ".join(errs[:5])])
    return report


def _built(cat, rep, model, edges):
    from . import instantiate as I
    return I.build(cat, rep, model, edges)


# ------------------------------------------------------------------------------------------------ schema

def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _obj(d, keys, path, errs, *, optional=()):
    if not isinstance(d, dict):
        errs.append(f"{path}: must be an object")
        return False
    miss = [k for k in keys if k not in d]
    extra = [k for k in d if k not in keys and k not in optional]
    if miss:
        errs.append(f"{path}: missing {miss}")
    if extra:
        errs.append(f"{path}: unexpected {extra}")
    return not miss and not extra


def validate_report(r: Any) -> list[str]:
    """Strict structural check of an investigation report. Returns the list of problems (empty = valid)."""
    errs: list[str] = []
    if not _obj(r, TOP_KEYS, "$", errs):
        return errs
    if r["investigation_report_version"] != REPORT_VERSION:
        errs.append("$.investigation_report_version: unsupported")
    if not _is_int(r["exit_code"]) or r["exit_code"] not in (0, 1):
        errs.append("$.exit_code: must be 0 or 1")
    for k in ("limitations", "problems"):
        if not isinstance(r[k], list) or not all(isinstance(x, str) for x in r[k]):
            errs.append(f"$.{k}: must be a list of strings")
    if not isinstance(r["units"], list):
        errs.append("$.units: must be a list")
        return errs
    unit_keys = ("unit_id", "unit_kind", "symptom_ids", "topics", "categories", "covered_topics", "instances", "set_status", "leader", "reason", "set_sentence",
                 "always_present", "missing_evidence", "assumptions", "propagation_facts", "diagnostic_asks")
    inst_keys = ("instance_id", "entry_id", "family", "mechanism", "tier", "status", "components", "satisfied_share", "review_status", "supplied_by", "bindings",
                 "binding_sources", "predictions", "ledger", "order", "claim_level", "claim_name", "claim_sentence", "uncapped_level", "caps", "cap_sentences",
                 "would_raise", "contradictions", "baseline_note", "contrast")
    for ui, u in enumerate(r["units"]):
        p = f"$.units[{ui}]"
        if not _obj(u, unit_keys, p, errs):
            continue
        if u["set_status"] not in SETS:
            errs.append(f"{p}.set_status: unknown")
        if u["leader"] is not None and not any(i["instance_id"] == u["leader"] for i in u["instances"] if isinstance(i, dict)):
            errs.append(f"{p}.leader: not an instance of this unit")
        for k in ("symptom_ids", "topics", "categories", "covered_topics", "missing_evidence"):
            if not isinstance(u[k], list) or not all(isinstance(x, str) for x in u[k]):
                errs.append(f"{p}.{k}: must be a list of strings")
        for ii, i in enumerate(u["instances"]):
            q = f"{p}.instances[{ii}]"
            if not _obj(i, inst_keys, q, errs):
                continue
            if i["tier"] not in TIERS:
                errs.append(f"{q}.tier: unknown")
            if not _is_int(i["claim_level"]) or not 0 <= i["claim_level"] <= K.MAX_REACHABLE:
                errs.append(f"{q}.claim_level: must be an integer from 0 to {K.MAX_REACHABLE}")
            elif i["claim_sentence"] != K.LEVEL_SENTENCE[i["claim_level"]]:
                errs.append(f"{q}.claim_sentence: not the fixed sentence of its level")
            if _is_int(i["claim_level"]) and _is_int(i["uncapped_level"]) and i["claim_level"] > i["uncapped_level"]:
                errs.append(f"{q}.claim_level: higher than the uncapped level")
            if i["tier"] == "contradicted" and _is_int(i["claim_level"]) and i["claim_level"] > 1:
                errs.append(f"{q}.claim_level: a contradicted hypothesis is shown at level 0 or 1 only")
            if u["leader"] == i["instance_id"] and i["tier"] != "supported":
                errs.append(f"{q}: a leader must be supported")
            for x in i["ledger"]:
                if not isinstance(x, dict) or x.get("kind") not in ("observed", "inferred", "assumption", "unconfirmed") or x.get("stance") not in ("supports", "contradicts", "neutral", "missing"):
                    errs.append(f"{q}.ledger: an item has an unknown kind or stance")
                    break
    if find_forbidden_keys(r):
        errs.append("$: a key uses a word this tool never emits (probability, score, rank, cause...)")
    errs += [f"sentence: {w!r} is not allowed at its level" for w in _sentence_violations(r)]
    return errs


def _sentence_violations(r: dict) -> list[str]:
    bad = []
    for u in r["units"]:
        for t, lvl in [(u["set_sentence"], 0)] + [(m["sentence"], 0) for m in u["always_present"]]:
            bad += K.gate(t, lvl)
        for i in u["instances"]:
            for t in [i["claim_sentence"], i["would_raise"], *i["cap_sentences"]]:
                bad += K.gate(t, i["claim_level"])
            for t in [i["mechanism"]] + [p["text"] for p in i["predictions"]]:
                bad += K.gate(t, 0) if find_forbidden(t) else []
    return bad


FORMATS = ("json", "markdown", "html")


def run_hypotheses(report_path, fmt: str, **kw) -> dict:
    """-> {"exit_code", "problems", "text"}. Never raises for bad input; nothing is written."""
    if fmt not in FORMATS:
        return {"exit_code": 2, "problems": [f"unknown format {clean_text(fmt)!r}; use one of {', '.join(FORMATS)}"], "text": ""}
    try:
        rep = investigate(report_path, **kw)
    except InvestigationError as e:
        return {"exit_code": e.code, "problems": e.problems, "text": ""}
    if fmt == "json":
        text = to_json(rep)
    else:
        from . import view
        text = view.render_markdown(rep) if fmt == "markdown" else view.render_html(rep)
    return {"exit_code": rep["exit_code"], "problems": [], "text": text}


def to_json(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


__all__ = ["run_hypotheses", "FORMATS", "investigate", "validate_report", "to_json", "InvestigationError", "REPORT_VERSION", "dependency_edges"]
