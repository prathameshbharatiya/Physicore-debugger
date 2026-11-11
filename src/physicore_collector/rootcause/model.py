"""The declared system model (M4a, schema 1): what the people who run the robot say about it. Nothing here is inferred, corrected or guessed.

Components with a role, signal bindings (role to topic and field), software and hardware versions, constraints (value range, rate, deadline,
redundancy group), known interventions, an operating context, and dependency edges in the M3d edge syntax. Every item records who declared it.
Loading never raises for bad input: it returns a problems list, and any problem makes the whole file invalid.

Untrusted input: the file is size-checked before it is read, YAML is pre-scanned event by event (no anchors, aliases, tags, merge keys, duplicate keys,
deep nesting) before anything is built. The scanning helpers are the ones of the accepted dependency loader (M3d), reused unchanged.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from physicore_collector.localization.dependencies import (
    MAX_NESTING, DepProblem as ModelProblem, _Collector, _check_keys, _is_int, _json_pairs, _no_const, _scan_json, _scan_yaml)
from physicore_collector.localization.vocabulary import EDGE_RELATIONS

SCHEMA_VERSION = 1
ROLES = ("sensor", "controller", "actuator", "planner", "driver", "middleware", "compute", "power")
VERSION_KINDS = ("software", "hardware")
CONSTRAINT_KINDS = ("value_range", "rate", "deadline", "redundancy_group")
INTERVENTION_KINDS = ("parameter_change", "restart")
CLOCK_DOMAINS = ("mcap_log_time",)
LIMITS = {"file_bytes": 1_048_576, "components": 500, "bindings": 2_000, "versions": 1_000, "constraints": 1_000, "interventions": 500, "edges": 5_000,
          "string_chars": 256, "context_chars": 2_000}

TOP_KEYS = ("system_model_schema_version", "declared_by", "system_name", "operating_context", "dependency_file_sha256", "components", "bindings", "versions",
            "constraints", "interventions", "edges")
COMPONENT_KEYS = ("id", "role", "description", "declared_by")
BINDING_KEYS = ("component", "role", "topic", "field", "type", "declared_by")
VERSION_KEYS = ("component", "kind", "name", "version", "digest", "declared_by")
INTERVENTION_KEYS = ("id", "kind", "component", "at_ns", "clock_domain", "parameter", "old_value", "new_value", "declared_by")
EDGE_KEYS = ("id", "from", "to", "relation", "window_ns", "declared_by")
CONSTRAINT_KEYS = {
    "value_range": ("id", "kind", "component", "role", "min", "max", "unit", "declared_by"),
    "rate": ("id", "kind", "component", "role", "min_hz", "max_hz", "declared_by"),
    "deadline": ("id", "kind", "trigger", "response", "deadline_ns", "declared_by"),
    "redundancy_group": ("id", "kind", "members", "min_healthy", "declared_by"),
}
REF_KEYS = ("component", "role")

_ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]*")
_TOPIC_RE = re.compile(r"/[A-Za-z0-9_/.~-]*")
_FIELD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")
_TYPE_RE = re.compile(r"[A-Za-z0-9_]+(/[A-Za-z0-9_]+){1,2}")
_SHA_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class SystemModel:
    content: dict[str, Any]          # normalised, every item with its resolved `declared_by` and `declared_by_source`
    file_sha256: str
    digest: str                      # hash of the normalised content: equal for files that say the same thing

    def topics(self) -> set[str]:
        """Every topic the model uses: binding topics and edge endpoints that are topics."""
        out = {b["topic"] for b in self.content["bindings"]}
        for e in self.content["edges"]:
            out |= {x for x in (e["from"], e["to"]) if x.startswith("/")}
        return out


@dataclass
class LoadedModel:
    model: SystemModel | None
    problems: list[ModelProblem]
    sha256: str | None = None
    problems_total: int = 0


def _bad(s: str, *, newline_ok: bool = False) -> bool:
    return any(unicodedata.category(c).startswith("C") and not (newline_ok and c == "\n") for c in s)


def _txt(v: Any, path: str, c: _Collector, limit: int, what: str, *, newline_ok: bool = False) -> str | None:
    if not isinstance(v, str):
        c.add(path, f"{what} must be a string")
        return None
    if len(v) > limit:
        c.add(path, f"{what} is longer than {limit} characters")
        return None
    if _bad(v, newline_ok=newline_ok):
        c.add(path, f"{what} contains control or formatting characters")
        return None
    return v


def _num(v: Any, path: str, c: _Collector, what: str, *, positive: bool = False) -> float | int | None:
    if type(v) not in (int, float) or (isinstance(v, float) and not math.isfinite(v)):   # type() is exact, so a boolean is refused here
        c.add(path, f"{what} must be a finite number")
        return None
    if positive and v <= 0:
        c.add(path, f"{what} must be greater than zero")
        return None
    return v


def _items(raw: dict, key: str, limit: int, c: _Collector) -> list:
    v = raw.get(key, [])
    if not isinstance(v, list):
        c.add(f"$.{key}", "must be a list")
        return []
    if len(v) > limit:
        c.add(f"$.{key}", f"more than {limit} entries")
        return []
    return v


def _id(v: Any, path: str, c: _Collector, sl: int, what: str) -> str | None:
    s = _txt(v, path, c, sl, what)
    if s is not None and not _ID_RE.fullmatch(s):
        c.add(path, f"{what} uses only letters, digits and _ . : - and does not start with '/'")
        return None
    return s


def _declared(d: dict, p: str, top: str | None, c: _Collector, sl: int) -> tuple[str | None, str]:
    """-> (declared_by, source): the item's own declaration, else the file's."""
    if "declared_by" in d:
        s = _txt(d["declared_by"], f"{p}.declared_by", c, sl, "declared_by")
        if s is not None and not s.strip():
            c.add(f"{p}.declared_by", "declared_by must not be empty")
            return None, "item"
        return s, "item"
    return top, "file"


def _ref(v: Any, p: str, c: _Collector, sl: int, bound: set[tuple[str, str]]) -> dict | None:
    if not isinstance(v, dict):
        c.add(p, "must be a mapping with component and role")
        return None
    _check_keys(v, REF_KEYS, p, c)
    comp = _id(v["component"], f"{p}.component", c, sl, "component") if "component" in v else None
    role = _id(v["role"], f"{p}.role", c, sl, "role") if "role" in v else None
    for k in REF_KEYS:
        if k not in v:
            c.add(f"{p}.{k}", "required")
    if comp is not None and role is not None:
        if (comp, role) not in bound:
            c.add(p, f"no binding for component '{comp}' and role '{role}'")
            return None
        return {"component": comp, "role": role}
    return None


def _validate(raw: Any, lim: dict, c: _Collector) -> dict | None:
    if not isinstance(raw, dict):
        c.add("$", "the file must contain a mapping at the top level")
        return None
    _check_keys(raw, TOP_KEYS, "$", c)
    sl = lim["string_chars"]
    if "system_model_schema_version" not in raw:
        c.add("$.system_model_schema_version", "required")
    elif not _is_int(raw["system_model_schema_version"]) or raw["system_model_schema_version"] != SCHEMA_VERSION:
        c.add("$.system_model_schema_version", f"must be the integer {SCHEMA_VERSION}")
    top = None
    if "declared_by" not in raw:
        c.add("$.declared_by", "required")
    else:
        top = _txt(raw["declared_by"], "$.declared_by", c, sl, "declared_by")
        if top is not None and not top.strip():
            c.add("$.declared_by", "declared_by must not be empty")
            top = None
    name = _txt(raw["system_name"], "$.system_name", c, sl, "system_name") if "system_name" in raw else None
    if "system_name" not in raw:
        c.add("$.system_name", "required")
    elif name is not None and not name.strip():
        c.add("$.system_name", "system_name must not be empty")
    context = None
    if "operating_context" in raw:
        context = _txt(raw["operating_context"], "$.operating_context", c, lim["context_chars"], "operating_context", newline_ok=True)
    dep_sha = None
    if "dependency_file_sha256" in raw:
        dep_sha = raw["dependency_file_sha256"]
        if not isinstance(dep_sha, str) or not _SHA_RE.fullmatch(dep_sha):
            c.add("$.dependency_file_sha256", "must be 64 lowercase hexadecimal characters")
            dep_sha = None

    # ---- components
    components, comp_ids = [], set()
    for i, d in enumerate(_items(raw, "components", lim["components"], c)):
        p = f"$.components[{i}]"
        if not isinstance(d, dict):
            c.add(p, "must be a mapping")
            continue
        _check_keys(d, COMPONENT_KEYS, p, c)
        cid = _id(d["id"], f"{p}.id", c, sl, "component id") if "id" in d else None
        if "id" not in d:
            c.add(f"{p}.id", "required")
        role = d.get("role")
        if "role" not in d:
            c.add(f"{p}.role", "required")
        elif not isinstance(role, str) or role not in ROLES:
            c.add(f"{p}.role", f"must be one of {list(ROLES)}")
            role = None
        desc = _txt(d["description"], f"{p}.description", c, sl, "description") if "description" in d else None
        by, src = _declared(d, p, top, c, sl)
        if cid is not None:
            if cid in comp_ids:
                c.add(f"{p}.id", f"duplicate component id '{cid}'")
                continue
            comp_ids.add(cid)
        if cid is not None and role is not None and by is not None:
            components.append({"id": cid, "role": role, "description": desc, "declared_by": by, "declared_by_source": src})

    # ---- bindings
    bindings, bound = [], set()
    for i, d in enumerate(_items(raw, "bindings", lim["bindings"], c)):
        p = f"$.bindings[{i}]"
        if not isinstance(d, dict):
            c.add(p, "must be a mapping")
            continue
        _check_keys(d, BINDING_KEYS, p, c)
        for req in ("component", "role", "topic"):
            if req not in d:
                c.add(f"{p}.{req}", "required")
        comp = d.get("component")
        if "component" in d and (not isinstance(comp, str) or comp not in comp_ids):
            c.add(f"{p}.component", "must name a declared component")
            comp = None
        role = _id(d["role"], f"{p}.role", c, sl, "role") if "role" in d else None
        topic = _txt(d["topic"], f"{p}.topic", c, sl, "topic") if "topic" in d else None
        if topic is not None and not _TOPIC_RE.fullmatch(topic):
            c.add(f"{p}.topic", "a topic starts with '/' and uses only letters, digits and _ / . ~ -")
            topic = None
        fld = None
        if "field" in d:
            fld = _txt(d["field"], f"{p}.field", c, sl, "field")
            if fld is not None and not _FIELD_RE.fullmatch(fld):
                c.add(f"{p}.field", "a field is a dotted path of names, for example header.stamp")
                fld = None
        typ = None
        if "type" in d:
            typ = _txt(d["type"], f"{p}.type", c, sl, "type")
            if typ is not None and not _TYPE_RE.fullmatch(typ):
                c.add(f"{p}.type", "a message type looks like pkg/msg/Name")
                typ = None
        by, src = _declared(d, p, top, c, sl)
        if isinstance(comp, str) and role is not None:
            if (comp, role) in bound:
                c.add(p, f"duplicate binding for component '{comp}' and role '{role}'")
                continue
            bound.add((comp, role))
        if comp is not None and role is not None and topic is not None and by is not None and not (("field" in d and fld is None) or ("type" in d and typ is None)):
            bindings.append({"component": comp, "role": role, "topic": topic, "field": fld, "type": typ, "declared_by": by, "declared_by_source": src})

    # ---- versions
    versions, seen_v = [], set()
    for i, d in enumerate(_items(raw, "versions", lim["versions"], c)):
        p = f"$.versions[{i}]"
        if not isinstance(d, dict):
            c.add(p, "must be a mapping")
            continue
        _check_keys(d, VERSION_KEYS, p, c)
        for req in ("component", "kind", "name", "version"):
            if req not in d:
                c.add(f"{p}.{req}", "required")
        comp = d.get("component")
        if "component" in d and (not isinstance(comp, str) or comp not in comp_ids):
            c.add(f"{p}.component", "must name a declared component")
            comp = None
        kind = d.get("kind")
        if "kind" in d and (not isinstance(kind, str) or kind not in VERSION_KINDS):
            c.add(f"{p}.kind", f"must be one of {list(VERSION_KINDS)}")
            kind = None
        nm = _txt(d["name"], f"{p}.name", c, sl, "name") if "name" in d else None
        ver = _txt(d["version"], f"{p}.version", c, sl, "version") if "version" in d else None
        for k, v in (("name", nm), ("version", ver)):
            if v is not None and not v.strip():
                c.add(f"{p}.{k}", f"{k} must not be empty")
                if k == "name":
                    nm = None
                else:
                    ver = None
        dg = None
        if "digest" in d:
            dg = d["digest"]
            if not isinstance(dg, str) or not _SHA_RE.fullmatch(dg):
                c.add(f"{p}.digest", "must be 64 lowercase hexadecimal characters")
                dg = None
        by, src = _declared(d, p, top, c, sl)
        if comp is not None and kind is not None and nm is not None:
            if (comp, kind, nm) in seen_v:
                c.add(p, f"duplicate version entry for component '{comp}', kind '{kind}', name '{nm}'")
                continue
            seen_v.add((comp, kind, nm))
        if None not in (comp, kind, nm, ver, by) and not ("digest" in d and dg is None):
            versions.append({"component": comp, "kind": kind, "name": nm, "version": ver, "digest": dg, "declared_by": by, "declared_by_source": src})

    # ---- constraints
    constraints, seen_c = [], set()
    for i, d in enumerate(_items(raw, "constraints", lim["constraints"], c)):
        p = f"$.constraints[{i}]"
        if not isinstance(d, dict):
            c.add(p, "must be a mapping")
            continue
        kind = d.get("kind")
        if "kind" not in d:
            c.add(f"{p}.kind", "required")
        elif not isinstance(kind, str) or kind not in CONSTRAINT_KINDS:
            c.add(f"{p}.kind", f"must be one of {list(CONSTRAINT_KINDS)}")
            kind = None
        if kind is None:
            continue
        _check_keys(d, CONSTRAINT_KEYS[kind], p, c)
        cid = _id(d["id"], f"{p}.id", c, sl, "constraint id") if "id" in d else None
        if "id" not in d:
            c.add(f"{p}.id", "required")
        if cid is not None:
            if cid in seen_c:
                c.add(f"{p}.id", f"duplicate constraint id '{cid}'")
                cid = None
            else:
                seen_c.add(cid)
        by, src = _declared(d, p, top, c, sl)
        out: dict[str, Any] = {"id": cid, "kind": kind, "declared_by": by, "declared_by_source": src}
        n_before = c.total
        if kind in ("value_range", "rate"):
            out.update(_ref({k: d[k] for k in REF_KEYS if k in d}, p, c, sl, bound) or {})
            lo_k, hi_k = ("min", "max") if kind == "value_range" else ("min_hz", "max_hz")
            lo = _num(d[lo_k], f"{p}.{lo_k}", c, lo_k) if lo_k in d else None
            hi = _num(d[hi_k], f"{p}.{hi_k}", c, hi_k) if hi_k in d else None
            if kind == "rate":
                for k, v in ((lo_k, lo), (hi_k, hi)):
                    if v is not None and v < 0:
                        c.add(f"{p}.{k}", f"{k} must not be negative")
            if lo_k not in d and hi_k not in d:
                c.add(p, f"give at least one of {lo_k} and {hi_k}")
            if lo is not None and hi is not None and lo > hi:
                c.add(p, f"{lo_k} is greater than {hi_k}")
            out.update({lo_k: lo, hi_k: hi})
            if kind == "value_range":
                out["unit"] = _txt(d["unit"], f"{p}.unit", c, sl, "unit") if "unit" in d else None
        elif kind == "deadline":
            for k in ("trigger", "response"):
                out[k] = _ref(d[k], f"{p}.{k}", c, sl, bound) if k in d else None
                if k not in d:
                    c.add(f"{p}.{k}", "required")
            if "deadline_ns" not in d:
                c.add(f"{p}.deadline_ns", "required")
            elif not _is_int(d["deadline_ns"]) or d["deadline_ns"] <= 0:
                c.add(f"{p}.deadline_ns", "must be a positive integer number of nanoseconds")
            else:
                out["deadline_ns"] = d["deadline_ns"]
        else:
            mem = d.get("members")
            members: list[str] = []
            if "members" not in d:
                c.add(f"{p}.members", "required")
            elif not isinstance(mem, list) or len(mem) < 2:
                c.add(f"{p}.members", "must be a list of at least two component ids")
            else:
                for j, m in enumerate(mem):
                    if not isinstance(m, str) or m not in comp_ids:
                        c.add(f"{p}.members[{j}]", "must name a declared component")
                    elif m in members:
                        c.add(f"{p}.members[{j}]", f"duplicate member '{m}'")
                    else:
                        members.append(m)
            mh = d.get("min_healthy")
            if "min_healthy" not in d:
                c.add(f"{p}.min_healthy", "required")
            elif not _is_int(mh) or mh < 1 or (isinstance(mem, list) and mh > len(mem)):
                c.add(f"{p}.min_healthy", "must be an integer from 1 to the number of members")
            out.update({"members": members, "min_healthy": mh})
        if c.total == n_before and cid is not None and by is not None:
            constraints.append(out)

    # ---- interventions
    interventions, seen_i = [], set()
    for i, d in enumerate(_items(raw, "interventions", lim["interventions"], c)):
        p = f"$.interventions[{i}]"
        if not isinstance(d, dict):
            c.add(p, "must be a mapping")
            continue
        _check_keys(d, INTERVENTION_KEYS, p, c)
        n_before = c.total
        iid = _id(d["id"], f"{p}.id", c, sl, "intervention id") if "id" in d else None
        for req in ("id", "kind", "component", "at_ns", "clock_domain"):
            if req not in d:
                c.add(f"{p}.{req}", "required")
        if iid is not None:
            if iid in seen_i:
                c.add(f"{p}.id", f"duplicate intervention id '{iid}'")
            seen_i.add(iid)
        kind = d.get("kind")
        if "kind" in d and (not isinstance(kind, str) or kind not in INTERVENTION_KINDS):
            c.add(f"{p}.kind", f"must be one of {list(INTERVENTION_KINDS)}")
            kind = None
        comp = d.get("component")
        if "component" in d and (not isinstance(comp, str) or comp not in comp_ids):
            c.add(f"{p}.component", "must name a declared component")
        at = d.get("at_ns")
        if "at_ns" in d and (not _is_int(at) or at < 0):
            c.add(f"{p}.at_ns", "must be a non-negative integer number of nanoseconds")
        cd = d.get("clock_domain")
        if "clock_domain" in d and (not isinstance(cd, str) or cd not in CLOCK_DOMAINS):
            c.add(f"{p}.clock_domain", f"must be one of {list(CLOCK_DOMAINS)}")
        extra = {}
        for k in ("parameter", "old_value", "new_value"):
            if k in d:
                if kind == "restart":
                    c.add(f"{p}.{k}", "a restart has no parameter or values")
                else:
                    extra[k] = _txt(d[k], f"{p}.{k}", c, sl, k)
        if kind == "parameter_change" and "parameter" not in d:
            c.add(f"{p}.parameter", "required for a parameter change")
        by, src = _declared(d, p, top, c, sl)
        if c.total == n_before and by is not None:
            interventions.append({"id": iid, "kind": kind, "component": comp, "at_ns": at, "clock_domain": cd, "parameter": extra.get("parameter"),
                                  "old_value": extra.get("old_value"), "new_value": extra.get("new_value"), "declared_by": by, "declared_by_source": src})

    # ---- edges (the M3d edge syntax; endpoints are topics or declared components)
    edges, seen_e = [], set()
    for i, e in enumerate(_items(raw, "edges", lim["edges"], c)):
        p = f"$.edges[{i}]"
        if not isinstance(e, dict):
            c.add(p, "must be a mapping")
            continue
        _check_keys(e, EDGE_KEYS, p, c)
        n_before = c.total
        for req in ("id", "from", "to", "relation"):
            if req not in e:
                c.add(f"{p}.{req}", "required")
        eid = _id(e["id"], f"{p}.id", c, sl, "edge id") if "id" in e else None
        if eid is not None:
            if eid in seen_e:
                c.add(f"{p}.id", f"duplicate edge id '{eid}'")
            seen_e.add(eid)
        ends = []
        for k in ("from", "to"):
            val = _txt(e[k], f"{p}.{k}", c, sl, f"edge {k}") if k in e else None
            if val is not None:
                if val.startswith("/"):
                    if not _TOPIC_RE.fullmatch(val):
                        c.add(f"{p}.{k}", "a topic uses only letters, digits and _ / . ~ -")
                elif val not in comp_ids:
                    c.add(f"{p}.{k}", f"'{val}' is neither a topic (starting with '/') nor a declared component")
            ends.append(val)
        if ends[0] is not None and ends[0] == ends[1]:
            c.add(p, "an edge from an endpoint to itself is not allowed")
        rel = e.get("relation")
        if "relation" in e and (not isinstance(rel, str) or rel not in EDGE_RELATIONS):
            c.add(f"{p}.relation", f"must be one of {list(EDGE_RELATIONS)}")
        win = None
        if "window_ns" in e:
            w = e["window_ns"]
            if not _is_int(w) or w <= 0:
                c.add(f"{p}.window_ns", "must be a positive integer number of nanoseconds")
            else:
                win = w
        by, src = _declared(e, p, top, c, sl)
        if c.total == n_before and by is not None:
            edges.append({"id": eid, "from": ends[0], "to": ends[1], "relation": rel, "window_ns": win, "declared_by": by, "declared_by_source": src})

    if c.total:
        return None
    # a redundancy group and a constraint may refer only to what exists: checked above; here only canonical ordering
    return {"system_model_schema_version": SCHEMA_VERSION, "declared_by": top, "system_name": name, "operating_context": context, "dependency_file_sha256": dep_sha,
            "components": sorted(components, key=lambda x: x["id"]), "bindings": sorted(bindings, key=lambda x: (x["component"], x["role"])),
            "versions": sorted(versions, key=lambda x: (x["component"], x["kind"], x["name"])), "constraints": sorted(constraints, key=lambda x: x["id"]),
            "interventions": sorted(interventions, key=lambda x: (x["at_ns"], x["id"])), "edges": sorted(edges, key=lambda x: x["id"])}


def content_digest(content: dict) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _fail(problems: list[ModelProblem], sha: str | None = None, total: int | None = None) -> LoadedModel:
    return LoadedModel(None, problems, sha, total if total is not None else len(problems))


def parse_system_model(data: bytes, *, name: str = "", limits: dict | None = None) -> LoadedModel:
    """Parse and validate the bytes of a system model file. Never raises."""
    lim = {**LIMITS, **(limits or {})}
    sha = hashlib.sha256(data).hexdigest()
    try:
        if len(data) > lim["file_bytes"]:
            return _fail([ModelProblem("$", f"file is larger than {lim['file_bytes']} bytes")], sha)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return _fail([ModelProblem("$", "file is not valid UTF-8 text")], sha)
        if "\x00" in text:
            return _fail([ModelProblem("$", "file contains NUL characters")], sha)
        if name.lower().endswith(".json"):
            msg = _scan_json(text, MAX_NESTING)
            if msg:
                return _fail([ModelProblem("$", msg)], sha)
            try:
                raw = json.loads(text, object_pairs_hook=_json_pairs, parse_constant=_no_const)
            except (ValueError, RecursionError) as e:
                return _fail([ModelProblem("$", f"JSON error: {str(e).splitlines()[0] if str(e) else 'invalid'}")], sha)
        else:
            msg = _scan_yaml(text, MAX_NESTING)
            if msg:
                return _fail([ModelProblem("$", msg)], sha)
            try:
                raw = yaml.safe_load(text)
            except (yaml.YAMLError, RecursionError) as e:
                return _fail([ModelProblem("$", f"YAML error: {str(e).splitlines()[0] if str(e) else 'invalid'}")], sha)
        if raw is None:
            return _fail([ModelProblem("$", "the file is empty")], sha)
        c = _Collector()
        content = _validate(raw, lim, c)
        if content is None:
            return _fail(c.items, sha, c.total)
        return LoadedModel(SystemModel(content, sha, content_digest(content)), [], sha, 0)
    except Exception as e:                                          # defensive: a loader must never raise on hostile input
        return _fail([ModelProblem("$", f"unexpected {type(e).__name__} while reading the file")], sha)


def read_regular_file(path: str | Path, limit: int) -> tuple[bytes | None, str | None]:
    """-> (bytes, None) or (None, reason). Refuses anything that is not a regular, non-symlink file before opening it (no blocking on a FIFO)."""
    p = Path(path)
    try:
        st = os.lstat(p)
    except OSError as e:
        return None, f"cannot read the file: {type(e).__name__}"
    if stat.S_ISLNK(st.st_mode):
        return None, "the file is a symbolic link; refusing"
    if not stat.S_ISREG(st.st_mode):
        return None, "the file is not a regular file"
    if st.st_size > limit:
        return None, f"file is larger than {limit} bytes"
    try:
        with open(p, "rb") as fh:
            return fh.read(limit + 1), None
    except OSError as e:
        return None, f"cannot read the file: {type(e).__name__}"


def load_system_model(path: str | Path, *, limits: dict | None = None) -> LoadedModel:
    lim = {**LIMITS, **(limits or {})}
    data, why = read_regular_file(path, lim["file_bytes"])
    if data is None:
        return _fail([ModelProblem("$", why or "cannot read the file")])
    return parse_system_model(data, name=Path(path).name, limits=lim)
