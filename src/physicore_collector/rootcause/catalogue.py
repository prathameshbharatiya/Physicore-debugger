"""The hypothesis catalogue (M4b): a closed, reviewable list of candidate mechanisms. A hypothesis is an instance of a catalogue entry bound to real
symptoms and components, never a free sentence.

One schema serves the built-in catalogue (v1, `catalogue_v1.py`) and a user extension file. User entries are marked `user_supplied`, must use ids that start
with `user.`, and cannot claim a review. Loading never raises for bad input; any problem makes the whole file invalid. Every sentence is checked against the
forbidden-wording list of M3d. The scanning helpers are those of the accepted dependency loader, as in `model.py`.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from physicore_collector.localization.dependencies import MAX_NESTING, _Collector, _check_keys, _is_int, _json_pairs, _no_const, _scan_json, _scan_yaml
from physicore_collector.localization.dependencies import DepProblem as CatalogueProblem
from physicore_collector.localization.symptoms import M3C_CATEGORIES_HANDLED as M3C_CATEGORIES
from physicore_collector.localization.vocabulary import find_forbidden

from .catalogue_v1 import CATALOGUE_V1_YAML
from .model import CONSTRAINT_KINDS, read_regular_file

SCHEMA_VERSION = 1
MECHANISM_IDS_V1 = ("upstream_source_silent", "message_loss_in_transport", "rate_degradation_under_load", "invalid_sensor_data_propagated", "command_not_followed",
                    "state_or_mode_transition_error", "configuration_mismatch", "timestamp_or_clock_fault", "resource_exhaustion", "hardware_degradation",
                    "software_exception_or_crash", "recording_or_observation_artifact")
FAMILIES = ("communication", "timing", "data_validity", "state", "configuration", "resource", "hardware", "software_fault", "observation_artifact")
M2_CATEGORIES = tuple(f"m2_{k}" for k in ("range", "tolerance", "equals", "transition", "event_response"))
SYMPTOM_CATEGORIES = tuple(M3C_CATEGORIES) + M2_CATEGORIES + ("signal_rate_of_change", "signal_stuck", "signal_discontinuity", "signal_oscillation", "cross_signal_inconsistency")
SLOT_SELECTS = ("symptom_topics", "declared_upstream_topics", "declared_downstream_topics", "sibling_topics")
ROLE_SELECT_RE = re.compile(r"binding_role:[A-Za-z0-9_][A-Za-z0-9_.:-]*")
RULE_OPS = ("symptom_present", "symptom_absent", "check_violated", "not_evaluable")
CHECK_KINDS = ("range", "tolerance", "equals", "transition", "event_response")
AVAILABILITY = ("usually_present", "usually_absent")
REVIEW_STATUSES = ("proposed", "reviewed")
SUPPLIED_BY = ("builtin", "user_supplied")
LIMITS = {"file_bytes": 524_288, "entries": 200, "string_chars": 240, "items_per_list": 30}

TOP_KEYS = ("catalogue_schema_version", "declared_by", "entries")
ENTRY_KEYS = ("id", "version", "family", "mechanism", "applies_to", "slots", "predictions", "checks", "evidence_requirements", "rivals", "review")
APPLIES_KEYS = ("symptom_categories", "component_roles")
SLOT_KEYS = ("name", "selects")
PRED_KEYS = ("id", "text", "rule")
CHECK_KEYS = ("id", "kind", "slot", "constraint_kind", "description")
REQ_KEYS = ("token", "description", "usual_availability")
REVIEW_KEYS = ("status", "note", "reviewed_by", "reviewed_on")
RULE_KEYS = {"symptom_present": ("op", "slot", "categories"), "symptom_absent": ("op", "slot", "categories"), "check_violated": ("op", "check"), "not_evaluable": ("op",)}

_ID_RE = re.compile(r"[a-z][a-z0-9_]*")
_USER_ID_RE = re.compile(r"user\.[a-z][a-z0-9_]*")
_NAME_RE = re.compile(r"[a-z][a-z0-9_]*")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class Catalogue:
    entries: tuple[dict, ...]            # sorted by id; every entry carries `supplied_by`
    digest: str                          # hash of the normalised entries
    sources: tuple[dict, ...]            # [{"supplied_by", "file_sha256", "declared_by"}] for the files that were loaded

    def by_id(self) -> dict[str, dict]:
        return {e["id"]: e for e in self.entries}


@dataclass
class LoadedCatalogue:
    entries: list[dict] | None
    problems: list[CatalogueProblem]
    sha256: str | None = None
    declared_by: str | None = None
    problems_total: int = 0


def _bad_chars(s: str) -> bool:
    import unicodedata
    return any(unicodedata.category(c).startswith("C") for c in s)


def _text(v, path, c, what, *, sentence=False):
    lim = LIMITS["string_chars"]
    if not isinstance(v, str):
        c.add(path, f"{what} must be a string")
        return None
    if not v.strip():
        c.add(path, f"{what} must not be empty")
        return None
    if len(v) > lim:
        c.add(path, f"{what} is longer than {lim} characters")
        return None
    if _bad_chars(v):
        c.add(path, f"{what} contains control or formatting characters")
        return None
    if sentence:
        if not v.endswith("."):
            c.add(path, f"{what} must be one sentence ending with a full stop")
            return None
        hits = find_forbidden(v)
        if hits:
            c.add(path, f"{what} uses wording that this tool never generates: {hits}")
            return None
    return v


def _list(d, key, path, c, *, required=True, minimum=0):
    if key not in d:
        if required:
            c.add(f"{path}.{key}", "required")
        return []
    v = d[key]
    if not isinstance(v, list):
        c.add(f"{path}.{key}", "must be a list")
        return []
    if len(v) > LIMITS["items_per_list"]:
        c.add(f"{path}.{key}", f"more than {LIMITS['items_per_list']} entries")
        return []
    if len(v) < minimum:
        c.add(f"{path}.{key}", f"needs at least {minimum} entr{'y' if minimum == 1 else 'ies'}")
    return v


def _enum_list(v, allowed, path, c, what):
    out = []
    for j, x in enumerate(v):
        if not isinstance(x, str) or x not in allowed:
            c.add(f"{path}[{j}]", f"{what} must be one of {list(allowed)}")
        elif x in out:
            c.add(f"{path}[{j}]", f"duplicate {what} '{x}'")
        else:
            out.append(x)
    return out


def _entry(d, i, supplied_by, c) -> dict | None:
    p = f"$.entries[{i}]"
    if not isinstance(d, dict):
        c.add(p, "must be a mapping")
        return None
    n0 = c.total
    _check_keys(d, ENTRY_KEYS, p, c)
    for req in ("id", "version", "family", "mechanism", "applies_to", "slots", "predictions", "evidence_requirements", "rivals"):
        if req not in d:
            c.add(f"{p}.{req}", "required")
    eid = d.get("id")
    if "id" in d:
        ok = isinstance(eid, str) and (_ID_RE.fullmatch(eid) and eid in MECHANISM_IDS_V1 if supplied_by == "builtin" else _USER_ID_RE.fullmatch(eid))
        if not ok:
            c.add(f"{p}.id", "a built-in id is one of the twelve mechanism ids" if supplied_by == "builtin" else "a user entry id looks like user.name (lowercase letters, digits, _)")
    ver = d.get("version")
    if "version" in d and (not _is_int(ver) or ver < 1):
        c.add(f"{p}.version", "must be an integer of at least 1")
    fam = d.get("family")
    if "family" in d and (not isinstance(fam, str) or fam not in FAMILIES):
        c.add(f"{p}.family", f"must be one of {list(FAMILIES)}")
    mech = _text(d["mechanism"], f"{p}.mechanism", c, "mechanism", sentence=True) if "mechanism" in d else None

    cats, roles = [], []
    ap = d.get("applies_to")
    if "applies_to" in d:
        if not isinstance(ap, dict):
            c.add(f"{p}.applies_to", "must be a mapping")
        else:
            _check_keys(ap, APPLIES_KEYS, f"{p}.applies_to", c)
            cats = _enum_list(_list(ap, "symptom_categories", f"{p}.applies_to", c, minimum=1), SYMPTOM_CATEGORIES, f"{p}.applies_to.symptom_categories", c, "symptom category")
            from .model import ROLES
            roles = _enum_list(_list(ap, "component_roles", f"{p}.applies_to", c, required=False), ROLES, f"{p}.applies_to.component_roles", c, "component role")

    slots, slot_names = [], set()
    for j, s in enumerate(_list(d, "slots", p, c, minimum=1)):
        sp = f"{p}.slots[{j}]"
        if not isinstance(s, dict):
            c.add(sp, "must be a mapping")
            continue
        _check_keys(s, SLOT_KEYS, sp, c)
        nm, sel = s.get("name"), s.get("selects")
        if "name" not in s or not isinstance(nm, str) or not _NAME_RE.fullmatch(nm):
            c.add(f"{sp}.name", "required: lowercase letters, digits, _")
        elif nm in slot_names:
            c.add(f"{sp}.name", f"duplicate slot name '{nm}'")
        else:
            slot_names.add(nm)
        if "selects" not in s or not isinstance(sel, str) or not (sel in SLOT_SELECTS or ROLE_SELECT_RE.fullmatch(sel)):
            c.add(f"{sp}.selects", f"required: one of {list(SLOT_SELECTS)} or binding_role:NAME")
        elif isinstance(nm, str):
            slots.append({"name": nm, "selects": sel})

    checks, check_ids = [], set()
    for j, k in enumerate(_list(d, "checks", p, c, required=False)):
        kp = f"{p}.checks[{j}]"
        if not isinstance(k, dict):
            c.add(kp, "must be a mapping")
            continue
        _check_keys(k, CHECK_KEYS, kp, c)
        for req in ("id", "kind", "slot", "description"):
            if req not in k:
                c.add(f"{kp}.{req}", "required")
        kid = k.get("id")
        if "id" in k:
            if not isinstance(kid, str) or not _NAME_RE.fullmatch(kid):
                c.add(f"{kp}.id", "lowercase letters, digits, _")
            elif kid in check_ids:
                c.add(f"{kp}.id", f"duplicate check id '{kid}'")
            else:
                check_ids.add(kid)
        if "kind" in k and (not isinstance(k["kind"], str) or k["kind"] not in CHECK_KINDS):
            c.add(f"{kp}.kind", f"must be one of {list(CHECK_KINDS)}")
        if "slot" in k and (not isinstance(k["slot"], str) or k["slot"] not in slot_names):
            c.add(f"{kp}.slot", "must name a slot of this entry")
        ck = k.get("constraint_kind")
        if "constraint_kind" in k and (not isinstance(ck, str) or ck not in CONSTRAINT_KINDS):
            c.add(f"{kp}.constraint_kind", f"must be one of {list(CONSTRAINT_KINDS)}")
        desc = _text(k["description"], f"{kp}.description", c, "description", sentence=True) if "description" in k else None
        checks.append({"id": kid, "kind": k.get("kind"), "slot": k.get("slot"), "constraint_kind": ck, "description": desc})

    preds, pred_ids = [], set()
    for j, q in enumerate(_list(d, "predictions", p, c, minimum=1)):
        qp = f"{p}.predictions[{j}]"
        if not isinstance(q, dict):
            c.add(qp, "must be a mapping")
            continue
        _check_keys(q, PRED_KEYS, qp, c)
        for req in ("id", "text", "rule"):
            if req not in q:
                c.add(f"{qp}.{req}", "required")
        qid = q.get("id")
        if "id" in q:
            if not isinstance(qid, str) or not _NAME_RE.fullmatch(qid):
                c.add(f"{qp}.id", "lowercase letters, digits, _")
            elif qid in pred_ids:
                c.add(f"{qp}.id", f"duplicate prediction id '{qid}'")
            else:
                pred_ids.add(qid)
        text = _text(q["text"], f"{qp}.text", c, "prediction", sentence=True) if "text" in q else None
        rule = None
        if "rule" in q:
            r = q["rule"]
            if not isinstance(r, dict) or "op" not in r or not isinstance(r["op"], str) or r["op"] not in RULE_OPS:
                c.add(f"{qp}.rule", f"must be a mapping with op one of {list(RULE_OPS)}")
            else:
                _check_keys(r, RULE_KEYS[r["op"]], f"{qp}.rule", c)
                rule = {"op": r["op"]}
                if r["op"] in ("symptom_present", "symptom_absent"):
                    if "slot" not in r or not isinstance(r["slot"], str) or r["slot"] not in slot_names:
                        c.add(f"{qp}.rule.slot", "must name a slot of this entry")
                    rcats = _enum_list(_list(r, "categories", f"{qp}.rule", c, minimum=1), SYMPTOM_CATEGORIES, f"{qp}.rule.categories", c, "symptom category")
                    rule.update(slot=r.get("slot"), categories=rcats)
                elif r["op"] == "check_violated":
                    if "check" not in r or not isinstance(r["check"], str) or r["check"] not in check_ids:
                        c.add(f"{qp}.rule.check", "must name a check of this entry")
                    rule.update(check=r.get("check"))
        preds.append({"id": qid, "text": text, "rule": rule})

    reqs, tokens = [], set()
    for j, r in enumerate(_list(d, "evidence_requirements", p, c)):
        rp = f"{p}.evidence_requirements[{j}]"
        if not isinstance(r, dict):
            c.add(rp, "must be a mapping")
            continue
        _check_keys(r, REQ_KEYS, rp, c)
        for req in REQ_KEYS:
            if req not in r:
                c.add(f"{rp}.{req}", "required")
        tk = r.get("token")
        if "token" in r:
            if not isinstance(tk, str) or not _NAME_RE.fullmatch(tk):
                c.add(f"{rp}.token", "lowercase letters, digits, _")
            elif tk in tokens:
                c.add(f"{rp}.token", f"duplicate token '{tk}'")
            else:
                tokens.add(tk)
        if "usual_availability" in r and (not isinstance(r["usual_availability"], str) or r["usual_availability"] not in AVAILABILITY):
            c.add(f"{rp}.usual_availability", f"must be one of {list(AVAILABILITY)}")
        desc = _text(r["description"], f"{rp}.description", c, "description", sentence=True) if "description" in r else None
        reqs.append({"token": tk, "description": desc, "usual_availability": r.get("usual_availability")})

    rivals = _list(d, "rivals", p, c)
    seen_r = []
    for j, rv in enumerate(rivals):
        if not isinstance(rv, str) or not (_ID_RE.fullmatch(rv) or _USER_ID_RE.fullmatch(rv)):
            c.add(f"{p}.rivals[{j}]", "must be an entry id")
        elif rv == eid:
            c.add(f"{p}.rivals[{j}]", "an entry is not its own rival")
        elif rv in seen_r:
            c.add(f"{p}.rivals[{j}]", f"duplicate rival '{rv}'")
        else:
            seen_r.append(rv)

    review = None
    if supplied_by == "user_supplied":
        if "review" in d:
            c.add(f"{p}.review", "a user entry cannot carry a review; it is always marked user_supplied")
        review = {"status": "user_supplied", "note": None, "reviewed_by": None, "reviewed_on": None}
    elif "review" not in d:
        c.add(f"{p}.review", "required")
    else:
        rv = d["review"]
        if not isinstance(rv, dict):
            c.add(f"{p}.review", "must be a mapping")
        else:
            _check_keys(rv, REVIEW_KEYS, f"{p}.review", c)
            st = rv.get("status")
            if "status" not in rv or not isinstance(st, str) or st not in REVIEW_STATUSES:
                c.add(f"{p}.review.status", f"required: one of {list(REVIEW_STATUSES)}")
            note = _text(rv["note"], f"{p}.review.note", c, "note", sentence=True) if "note" in rv else None
            if "note" not in rv:
                c.add(f"{p}.review.note", "required")
            by, on = rv.get("reviewed_by"), rv.get("reviewed_on")
            if st == "reviewed":
                if not isinstance(by, str) or not by.strip() or len(by) > LIMITS["string_chars"] or _bad_chars(by):
                    c.add(f"{p}.review.reviewed_by", "required for a reviewed entry")
                if not isinstance(on, str) or not _DATE_RE.fullmatch(on):
                    c.add(f"{p}.review.reviewed_on", "required for a reviewed entry: YYYY-MM-DD")
            elif st == "proposed" and ("reviewed_by" in rv or "reviewed_on" in rv):
                c.add(f"{p}.review", "a proposed entry has no reviewer or date")
            review = {"status": st, "note": note, "reviewed_by": by, "reviewed_on": on}

    if c.total != n0:
        return None
    return {"id": eid, "version": ver, "family": fam, "mechanism": mech,
            "applies_to": {"symptom_categories": sorted(cats), "component_roles": sorted(roles)},
            "slots": sorted(slots, key=lambda x: x["name"]), "predictions": sorted(preds, key=lambda x: x["id"]), "checks": sorted(checks, key=lambda x: x["id"]),
            "evidence_requirements": sorted(reqs, key=lambda x: x["token"]), "rivals": sorted(seen_r), "review": review, "supplied_by": supplied_by}


def _validate(raw, supplied_by, c) -> tuple[list[dict] | None, str | None]:
    if not isinstance(raw, dict):
        c.add("$", "the file must contain a mapping at the top level")
        return None, None
    _check_keys(raw, TOP_KEYS, "$", c)
    if "catalogue_schema_version" not in raw:
        c.add("$.catalogue_schema_version", "required")
    elif not _is_int(raw["catalogue_schema_version"]) or raw["catalogue_schema_version"] != SCHEMA_VERSION:
        c.add("$.catalogue_schema_version", f"must be the integer {SCHEMA_VERSION}")
    by = None
    if "declared_by" not in raw:
        c.add("$.declared_by", "required")
    else:
        by = _text(raw["declared_by"], "$.declared_by", c, "declared_by")
    items = raw.get("entries")
    entries: list[dict] = []
    if "entries" not in raw:
        c.add("$.entries", "required")
    elif not isinstance(items, list):
        c.add("$.entries", "must be a list")
    elif len(items) > LIMITS["entries"]:
        c.add("$.entries", f"more than {LIMITS['entries']} entries")
    elif not items:
        c.add("$.entries", "needs at least one entry")
    else:
        seen = set()
        for i, d in enumerate(items):
            e = _entry(d, i, supplied_by, c)
            if e is not None:
                if e["id"] in seen:
                    c.add(f"$.entries[{i}].id", f"duplicate entry id '{e['id']}'")
                else:
                    seen.add(e["id"])
                    entries.append(e)
        if supplied_by == "builtin" and not c.total and sorted(seen) != sorted(MECHANISM_IDS_V1):
            c.add("$.entries", "the built-in catalogue holds exactly the twelve mechanism entries")
    if c.total:
        return None, by
    return sorted(entries, key=lambda e: e["id"]), by


def parse_catalogue(data: bytes, *, name: str = "", supplied_by: str = "user_supplied") -> LoadedCatalogue:
    """Parse and validate the bytes of a catalogue file. Never raises."""
    if supplied_by not in SUPPLIED_BY:
        raise ValueError("supplied_by must be builtin or user_supplied")
    sha = hashlib.sha256(data).hexdigest()

    def fail(problems, total=None):
        return LoadedCatalogue(None, problems, sha, None, total if total is not None else len(problems))
    try:
        if len(data) > LIMITS["file_bytes"]:
            return fail([CatalogueProblem("$", f"file is larger than {LIMITS['file_bytes']} bytes")])
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return fail([CatalogueProblem("$", "file is not valid UTF-8 text")])
        if "\x00" in text:
            return fail([CatalogueProblem("$", "file contains NUL characters")])
        if name.lower().endswith(".json"):
            msg = _scan_json(text, MAX_NESTING)
            if msg:
                return fail([CatalogueProblem("$", msg)])
            try:
                raw = json.loads(text, object_pairs_hook=_json_pairs, parse_constant=_no_const)
            except (ValueError, RecursionError) as e:
                return fail([CatalogueProblem("$", f"JSON error: {str(e).splitlines()[0] if str(e) else 'invalid'}")])
        else:
            msg = _scan_yaml(text, MAX_NESTING)
            if msg:
                return fail([CatalogueProblem("$", msg)])
            try:
                raw = yaml.safe_load(text)
            except (yaml.YAMLError, RecursionError) as e:
                return fail([CatalogueProblem("$", f"YAML error: {str(e).splitlines()[0] if str(e) else 'invalid'}")])
        if raw is None:
            return fail([CatalogueProblem("$", "the file is empty")])
        c = _Collector()
        entries, by = _validate(raw, supplied_by, c)
        if entries is None:
            return fail(c.items, c.total)
        return LoadedCatalogue(entries, [], sha, by, 0)
    except Exception as e:                                          # a loader must never raise on hostile input
        return fail([CatalogueProblem("$", f"unexpected {type(e).__name__} while reading the file")])


def load_catalogue_file(path: str | Path) -> LoadedCatalogue:
    """A user extension file: read only if it is a regular file, then parsed as `user_supplied`."""
    data, why = read_regular_file(path, LIMITS["file_bytes"])
    if data is None:
        return LoadedCatalogue(None, [CatalogueProblem("$", why or "cannot read the file")], None, None, 1)
    return parse_catalogue(data, name=Path(path).name, supplied_by="user_supplied")


def digest_of(entries) -> str:
    return hashlib.sha256(json.dumps(list(entries), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def builtin() -> Catalogue:
    """The built-in catalogue v1. It is parsed with the same schema as any user file; a defect in it fails loudly."""
    r = parse_catalogue(CATALOGUE_V1_YAML.encode("utf-8"), name="catalogue_v1.yaml", supplied_by="builtin")
    if r.entries is None:
        raise RuntimeError("the built-in catalogue is invalid: " + "; ".join(f"{p.path}: {p.message}" for p in r.problems))
    _check_rivals(r.entries, r.entries)
    return Catalogue(tuple(r.entries), digest_of(r.entries), ({"supplied_by": "builtin", "file_sha256": r.sha256, "declared_by": r.declared_by},))


def _check_rivals(entries, universe) -> list[str]:
    ids = {e["id"] for e in universe}
    bad = [f"entry '{e['id']}' names a rival '{r}' that is not in the catalogue" for e in entries for r in e["rivals"] if r not in ids]
    if bad:
        raise ValueError("; ".join(bad))
    return bad


def merged(user: LoadedCatalogue | None = None) -> Catalogue:
    """Built-in entries plus the entries of a successfully loaded user extension. Raises ValueError if the user file names an unknown rival."""
    b = builtin()
    if user is None:
        return b
    if user.entries is None:
        raise ValueError("the user catalogue did not load")
    allentries = sorted(list(b.entries) + list(user.entries), key=lambda e: e["id"])
    _check_rivals(user.entries, allentries)
    return Catalogue(tuple(allentries), digest_of(allentries), b.sources + ({"supplied_by": "user_supplied", "file_sha256": user.sha256, "declared_by": user.declared_by},))
