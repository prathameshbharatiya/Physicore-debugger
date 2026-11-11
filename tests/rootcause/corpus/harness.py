"""M4-0 evaluation harness: scores a diagnostic result against the frozen truth record of a corpus scenario.

The input is a small, strict "score input" (schema 1) that each later milestone fills from its real report through an adapter; the
harness itself knows nothing about how hypotheses were produced. Results on the synthetic corpus show that the software follows its
definitions. They are not diagnostic accuracy on real robots and must never be reported as such.

score input, schema 1
    {"score_input_version": 1, "scenario_id": str,
     "sets": [{"set_id": str, "topics": [str], "hypotheses": [Hypothesis]}],
     "named_missing_evidence": [str], "sentences": [{"text": str, "claim_level": int}]}
    Hypothesis = {"class": str, "tier": "leading"|"supported"|"possible"|"contradicted"|"not_assessed",
                  "status": "assessed"|"cannot_be_assessed"|"unresolved", "source_topic": str|null}
"""
from __future__ import annotations

import json
from typing import Any

from physicore_collector.localization.vocabulary import find_forbidden

from .generate import SPEC

TIERS = ("leading", "supported", "possible", "contradicted", "not_assessed")
STATUSES = ("assessed", "cannot_be_assessed", "unresolved")
CLASSES = tuple(SPEC["mechanism_classes"])
UNRESOLVED = "unresolved"        # the explicit always-present member
LEVELS = range(0, 6)
TOP_KEYS = {"score_input_version", "scenario_id", "sets", "named_missing_evidence", "sentences"}
SET_KEYS = {"set_id", "topics", "hypotheses"}
HYP_KEYS = {"class", "tier", "status", "source_topic"}
SENT_KEYS = {"text", "claim_level"}


class ScoreInputError(ValueError):
    pass


def _need(cond: bool, msg: str):
    if not cond:
        raise ScoreInputError(msg)


def validate_input(si: Any) -> dict:
    _need(isinstance(si, dict) and set(si) == TOP_KEYS, f"score input keys must be exactly {sorted(TOP_KEYS)}")
    _need(si["score_input_version"] == 1, "unsupported score_input_version")
    _need(isinstance(si["scenario_id"], str) and si["scenario_id"], "scenario_id must be a non-empty string")
    _need(isinstance(si["sets"], list), "sets must be a list")
    seen = set()
    for s in si["sets"]:
        _need(isinstance(s, dict) and set(s) == SET_KEYS, f"set keys must be exactly {sorted(SET_KEYS)}")
        _need(isinstance(s["set_id"], str) and s["set_id"] not in seen, "set_id must be a unique string")
        seen.add(s["set_id"])
        _need(isinstance(s["topics"], list) and all(isinstance(t, str) for t in s["topics"]), "topics must be a list of strings")
        _need(isinstance(s["hypotheses"], list), "hypotheses must be a list")
        for h in s["hypotheses"]:
            _need(isinstance(h, dict) and set(h) == HYP_KEYS, f"hypothesis keys must be exactly {sorted(HYP_KEYS)}")
            _need(h["class"] in CLASSES or h["class"] == UNRESOLVED, f"unknown class {h['class']!r}")
            _need(h["tier"] in TIERS, f"unknown tier {h['tier']!r}")
            _need(h["status"] in STATUSES, f"unknown status {h['status']!r}")
            _need(h["source_topic"] is None or isinstance(h["source_topic"], str), "source_topic must be a string or null")
            _need(h["tier"] != "leading" or (h["status"] == "assessed" and h["class"] != UNRESOLVED), "only an assessed mechanism can lead")
    _need(isinstance(si["named_missing_evidence"], list) and all(isinstance(x, str) for x in si["named_missing_evidence"]), "named_missing_evidence must be a list of strings")
    _need(isinstance(si["sentences"], list), "sentences must be a list")
    for x in si["sentences"]:
        _need(isinstance(x, dict) and set(x) == SENT_KEYS, f"sentence keys must be exactly {sorted(SENT_KEYS)}")
        _need(isinstance(x["text"], str) and isinstance(x["claim_level"], int) and not isinstance(x["claim_level"], bool) and x["claim_level"] in LEVELS, "bad sentence")
    return si


def _wording_violations(si: dict, released: dict[int, set[str]]) -> int:
    """Forbidden words in a sentence, except words that the sentence's own claim level releases (the table comes from the claim ladder, M4d)."""
    n = 0
    for x in si["sentences"]:
        ok = set()
        for lvl, words in released.items():
            if x["claim_level"] >= lvl:
                ok |= {w.lower() for w in words}
        n += sum(1 for w in find_forbidden(x["text"]) if w.lower() not in ok)
    return n


def score_scenario(truth: dict, si: dict, *, released: dict[int, set[str]] | None = None) -> dict:
    """Score one result against one truth record. Pure function; the answer is a plain dict of measures."""
    validate_input(si)
    _need(si["scenario_id"] == truth["scenario_id"], "score input belongs to another scenario")
    released = released or {}
    sets, kind = si["sets"], truth["answer_kind"]
    leaders = [(s, h) for s in sets for h in s["hypotheses"] if h["tier"] == "leading"]
    acceptable = set(truth["acceptable_leaders"])
    forbidden_c = set(truth["forbidden_leader_classes"])
    if kind == "mechanism":
        wrong = [h for _s, h in leaders if h["class"] not in acceptable or h["class"] in forbidden_c]
    else:
        wrong = [h for _s, h in leaders]                      # unresolved or no failure: no leader is allowed at all
    out: dict[str, Any] = {"scenario_id": truth["scenario_id"], "family_id": truth["family_id"], "split": truth["split"], "answer_kind": kind,
                           "hypothesis_count": sum(len(s["hypotheses"]) for s in sets), "set_count": len(sets), "leader_count": len(leaders),
                           "confident_wrong_leader": bool(wrong)}
    # truth in the competing set (mechanism scenarios): every fault's true class appears in a set that covers the fault topic
    if kind == "mechanism":
        found = []
        for f in truth["faults"]:
            cover = [s for s in sets if f["topic"] in s["topics"]]
            found.append(any(h["class"] == f["true_class"] for s in cover for h in s["hypotheses"]))
        out["truth_in_set"] = all(found) if found else None
        out["faults_found"] = sum(found)
        out["faults_total"] = len(found)
    else:
        out["truth_in_set"] = None
    # links that must not be drawn: no set may hold both topics of a forbidden pair
    out["forbidden_link_violations"] = sum(1 for a, b in truth["forbidden_links"] for s in sets if a in s["topics"] and b in s["topics"])
    # abstention (unresolved) and silence (no failure)
    if kind == "unresolved":
        unassessed_ok = True
        for cls in truth["candidates_expected_unassessable"]:
            for s in sets:
                for h in s["hypotheses"]:
                    if h["class"] == cls and not (h["status"] == "cannot_be_assessed" and h["tier"] == "not_assessed"):
                        unassessed_ok = False        # an unobservable mechanism must be reported as "cannot be assessed", never as supported
        out["correct_abstention"] = (not leaders) and unassessed_ok
    elif kind == "no_failure":
        out["correct_abstention"] = (not sets) and not leaders
    else:
        out["correct_abstention"] = None
    # coverage statement: the report names the measurements it lacks
    req = set(truth["required_missing_evidence"])
    out["missing_evidence_named"] = None if not req else req <= set(si["named_missing_evidence"])
    out["wording_violations"] = _wording_violations(si, released)
    return out


def aggregate(results: list[dict]) -> dict:
    """Counts over a list of scenario results. Deterministic: sorted keys, integers and exact fractions as strings."""
    def count(key, want=True):
        vals = [r[key] for r in results if r[key] is not None]
        return {"yes": sum(1 for v in vals if v is want), "of": len(vals)}
    by_family = {}
    for r in results:
        by_family.setdefault(r["family_id"], []).append(r)
    return {
        "scenarios": len(results),
        "truth_in_set": count("truth_in_set"),
        "confident_wrong_leader": {"yes": sum(1 for r in results if r["confident_wrong_leader"]), "of": len(results)},
        "correct_abstention": count("correct_abstention"),
        "missing_evidence_named": count("missing_evidence_named"),
        "forbidden_link_violations": sum(r["forbidden_link_violations"] for r in results),
        "wording_violations": sum(r["wording_violations"] for r in results),
        "families": sorted(by_family),
    }


def report_json(results: list[dict]) -> str:
    return json.dumps({"results": sorted(results, key=lambda r: r["scenario_id"]), "aggregate": aggregate(results),
                       "note": "synthetic corpus: shows that the software follows its definitions, not diagnostic accuracy on real robots"},
                      indent=2, sort_keys=True) + "\n"


def score_input_for(truth: dict, **kw) -> dict:
    """A well-formed score input with chosen contents (helper for tests and adapters)."""
    base = {"score_input_version": 1, "scenario_id": truth["scenario_id"], "sets": [], "named_missing_evidence": [], "sentences": []}
    base.update(kw)
    return base
