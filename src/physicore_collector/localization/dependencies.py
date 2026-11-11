"""The declared dependency file and its graph (DESIGN 3.2, FR-DEP-01..06). Relations between topics come ONLY from this file: nothing is ever inferred from the
recording, topic names or message types. Loading never raises for bad input: it returns a problems list (any problem => the file is invalid => nothing is analysed).
Untrusted input: the file is size-checked before it is read, YAML is pre-scanned event by event (no anchors, aliases, tags, merge keys, duplicate keys, deep nesting)
before anything is constructed, and every structure walked here is walked iteratively."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .vocabulary import DEFAULT_LIMITS, EDGE_RELATIONS

SCHEMA_VERSION = 1
MAX_NESTING = 8                      # the schema is at most 4 levels deep (top > edges > edge / bindings > binding > identity)
MAX_EVENTS = 400_000
MAX_PROBLEMS = 100

TOP_KEYS = ("dependency_schema_version", "declared_by", "nodes", "bindings", "edges")
NODE_KEYS = ("id",)
BINDING_KEYS = ("node", "topic", "identity")
EDGE_KEYS = ("id", "from", "to", "relation", "window_ns")
IDENTITY_KEYS = ("name", "hardware_id", "goal_id", "battery_serial", "battery_location")
NAME_KINDS = ("logger_name", "diagnostic_name")                    # what a binding key `name` can match
IDENTITY_KEY_TO_KINDS = {"name": NAME_KINDS, "hardware_id": ("hardware_id",), "goal_id": ("goal_id",),
                         "battery_serial": ("battery_serial",), "battery_location": ("battery_location",)}

_ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]*")
_TOPIC_RE = re.compile(r"/[A-Za-z0-9_/.~-]*")


@dataclass(frozen=True)
class DepProblem:
    path: str
    message: str


@dataclass(frozen=True)
class Edge:
    id: str
    src: str
    dst: str
    relation: str
    window_ns: int | None


@dataclass(frozen=True)
class Binding:
    node: str
    topic: str
    identity: tuple[tuple[str, str], ...]          # sorted (key, value) pairs; all must match


@dataclass
class DependencyGraph:
    declared_by: str
    nodes: tuple[str, ...]
    bindings: tuple[Binding, ...]
    edges: tuple[Edge, ...]                         # sorted by id
    sha256: str
    semantic_hash: str
    out: dict[str, list[Edge]] = field(default_factory=dict)       # outgoing edges per endpoint, sorted by (dst, id)
    inc: dict[str, list[Edge]] = field(default_factory=dict)
    _reach_cache: dict[str, frozenset[str]] = field(default_factory=dict, repr=False, compare=False)       # bounded memo of `reaches` (the graph is immutable)

    @property
    def endpoints(self) -> list[str]:
        return sorted(set(self.out) | set(self.inc))

    def node_for(self, topic: str, identities: dict[str, str]) -> tuple[str, str]:
        """-> (endpoint, status): `bound` (exactly one declared node matches), `topic` (no binding applies), `ambiguous` (bindings to different nodes
        match; fail closed: the topic itself is used)."""
        hits = set()
        for b in self.bindings:
            if b.topic != topic:
                continue
            if all(any(identities.get(kind) == val for kind in IDENTITY_KEY_TO_KINDS[key]) for key, val in b.identity):
                hits.add(b.node)
        if not hits:
            return topic, "topic"
        if len(hits) > 1:
            return topic, "ambiguous"
        return next(iter(hits)), "bound"

    def ancestors(self, endpoint: str) -> list[str]:
        """Every endpoint with a declared path to `endpoint` (iterative; excludes itself unless it lies on a cycle through itself)."""
        seen: dict[str, None] = {}
        stack = [endpoint]
        while stack:
            cur = stack.pop()
            for e in self.inc.get(cur, ()):
                if e.src not in seen:
                    seen[e.src] = None
                    stack.append(e.src)
        return sorted(seen)

    def reaches(self, endpoint: str) -> set[str]:
        """Endpoints from which `endpoint` is reachable (including itself). Memoised for at most 256 endpoints (bounded memory)."""
        hit = self._reach_cache.get(endpoint)
        if hit is not None:
            return hit
        seen = {endpoint}
        stack = [endpoint]
        while stack:
            cur = stack.pop()
            for e in self.inc.get(cur, ()):
                if e.src not in seen:
                    seen.add(e.src)
                    stack.append(e.src)
        if len(self._reach_cache) >= 256:
            self._reach_cache.clear()
        out = self._reach_cache[endpoint] = frozenset(seen)
        return out


@dataclass
class WorkBudget:
    """Global bound on candidate-path processing (clarification B): shared by every enumeration of a run, so per-pair limits cannot multiply."""
    limit: int
    used: int = 0

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit

    def take(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


@dataclass
class PathResult:
    paths: list[tuple[Edge, ...]]
    complete: bool                    # True iff every simple path within the depth bound was enumerated
    depth_truncated: bool             # some route was cut by the depth bound
    pair_cap_reached: bool
    work_exhausted: bool

    @property
    def exact_total(self) -> int | None:
        return len(self.paths) if self.complete and not self.depth_truncated else None


def enumerate_paths(graph: DependencyGraph, src: str, dst: str, *, max_depth: int, max_paths: int, budget: WorkBudget) -> PathResult:
    """Simple directed paths src -> dst in a deterministic order (edges by (dst, id)), iteratively, bounded by depth, by `max_paths` for this pair and by
    the shared work budget. Only endpoints that can still reach `dst` are expanded, so work is proportional to the paths found."""
    paths: list[tuple[Edge, ...]] = []
    if src == dst:
        return PathResult(paths, True, False, False, False)
    useful = graph.reaches(dst)
    if src not in useful:
        return PathResult(paths, True, False, False, False)
    depth_cut = pair_cap = work_out = False
    on_path = {src}
    stack: list[tuple[str, int]] = [(src, 0)]          # (endpoint, next outgoing-edge index)
    trail: list[Edge] = []
    while stack:
        node, i = stack[-1]
        outs = graph.out.get(node, ())
        if i >= len(outs):
            stack.pop()
            on_path.discard(node)
            if trail:
                trail.pop()
            continue
        stack[-1] = (node, i + 1)
        e = outs[i]
        if e.dst not in useful or e.dst in on_path:
            continue
        if not budget.take():
            work_out = True
            break
        if len(trail) + 1 > max_depth:
            depth_cut = True
            continue
        if e.dst == dst:
            if len(paths) >= max_paths:
                pair_cap = True
                break
            paths.append(tuple(trail) + (e,))
            continue
        trail.append(e)
        on_path.add(e.dst)
        stack.append((e.dst, 0))
    return PathResult(paths, not (pair_cap or work_out), depth_cut, pair_cap, work_out)


# ------------------------------------------------------------------------------------------------ loading

@dataclass
class LoadedDependencies:
    graph: DependencyGraph | None
    problems: list[DepProblem]
    sha256: str | None = None
    problems_total: int = 0


def _bad_text(s: str) -> bool:
    return any(unicodedata.category(c).startswith("C") for c in s)


def _scan_json(text: str, limit: int) -> str | None:
    depth, in_str, esc = 0, False, False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "[{":
            depth += 1
            if depth > limit:
                return f"nesting deeper than {limit}"
        elif ch in "]}":
            depth -= 1
    return None


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict:
    d: dict = {}
    for k, v in pairs:
        if k in d:
            raise ValueError(f"duplicate key '{k}'")
        d[k] = v
    return d


def _no_const(name: str) -> Any:
    raise ValueError(f"the constant {name} is not allowed")


def _scan_yaml(text: str, limit: int) -> str | None:
    try:
        stack: list[dict] = []
        docs = 0
        n = 0
        for ev in yaml.parse(text, Loader=yaml.SafeLoader):
            n += 1
            if n > MAX_EVENTS:
                return "document too large"
            if isinstance(ev, yaml.DocumentStartEvent):
                docs += 1
                if docs > 1:
                    return "more than one YAML document"
                continue
            if isinstance(ev, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                stack.pop()
                continue
            if not isinstance(ev, (yaml.ScalarEvent, yaml.AliasEvent, yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                continue
            if isinstance(ev, yaml.AliasEvent):
                return "YAML aliases are not allowed"
            if getattr(ev, "anchor", None) is not None:
                return "YAML anchors are not allowed"
            if getattr(ev, "tag", None) is not None:
                return "explicit YAML tags are not allowed"
            if stack and stack[-1]["kind"] == "map":
                top = stack[-1]
                is_key = top["idx"] % 2 == 0
                top["idx"] += 1
                if is_key:
                    if not isinstance(ev, yaml.ScalarEvent):
                        return "mapping keys must be plain scalars"
                    if ev.value == "<<":
                        return "YAML merge keys are not allowed"
                    if ev.value in top["keys"]:
                        return f"duplicate key '{ev.value}'"
                    top["keys"].add(ev.value)
            if isinstance(ev, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                stack.append({"kind": "map" if isinstance(ev, yaml.MappingStartEvent) else "seq", "idx": 0, "keys": set()})
                if len(stack) > limit:
                    return f"nesting deeper than {limit}"
        return None
    except yaml.YAMLError as e:
        return f"YAML syntax error: {str(e).splitlines()[0] if str(e) else 'invalid'}"
    except RecursionError:
        return "nesting too deep"


class _Collector:
    def __init__(self) -> None:
        self.items: list[DepProblem] = []
        self.total = 0

    def add(self, path: str, message: str) -> None:
        self.total += 1
        if len(self.items) < MAX_PROBLEMS:
            self.items.append(DepProblem(path, message))


def _is_int(v: Any) -> bool:
    return type(v) is int


def _check_keys(d: dict, allowed: tuple[str, ...], path: str, c: _Collector) -> None:
    for k in d:
        if not isinstance(k, str):
            c.add(path, f"key {k!r} is not a string")
        elif k not in allowed:
            c.add(f"{path}.{k}", "unknown key")


def _text(v: Any, path: str, c: _Collector, limit: int, what: str) -> str | None:
    if not isinstance(v, str):
        c.add(path, f"{what} must be a string")
        return None
    if len(v) > limit:
        c.add(path, f"{what} is longer than {limit} characters")
        return None
    if _bad_text(v):
        c.add(path, f"{what} contains control or formatting characters")
        return None
    return v


def _validate(raw: Any, limits: dict, c: _Collector) -> dict | None:
    """-> normalised content (plain dict) or None; problems are collected, never raised."""
    if not isinstance(raw, dict):
        c.add("$", "the file must contain a mapping at the top level")
        return None
    _check_keys(raw, TOP_KEYS, "$", c)
    sl = limits["dependency_string_chars"]
    v = raw.get("dependency_schema_version")
    if "dependency_schema_version" not in raw:
        c.add("$.dependency_schema_version", "required")
    elif not _is_int(v) or v != SCHEMA_VERSION:
        c.add("$.dependency_schema_version", f"must be the integer {SCHEMA_VERSION}")
    declared_by = None
    if "declared_by" not in raw:
        c.add("$.declared_by", "required")
    else:
        declared_by = _text(raw["declared_by"], "$.declared_by", c, sl, "declared_by")
        if declared_by is not None and not declared_by.strip():
            c.add("$.declared_by", "declared_by must not be empty")
            declared_by = None
    # ---- nodes
    node_ids: list[str] = []
    nodes_raw = raw.get("nodes", [])
    if not isinstance(nodes_raw, list):
        c.add("$.nodes", "must be a list")
        nodes_raw = []
    if len(nodes_raw) > limits["dependency_nodes"]:
        c.add("$.nodes", f"more than {limits['dependency_nodes']} nodes")
        nodes_raw = []
    seen_nodes: set[str] = set()
    for i, n in enumerate(nodes_raw):
        p = f"$.nodes[{i}]"
        if not isinstance(n, dict):
            c.add(p, "must be a mapping")
            continue
        _check_keys(n, NODE_KEYS, p, c)
        if "id" not in n:
            c.add(f"{p}.id", "required")
            continue
        nid = _text(n["id"], f"{p}.id", c, sl, "node id")
        if nid is None:
            continue
        if not _ID_RE.fullmatch(nid):
            c.add(f"{p}.id", "a node id uses only letters, digits and _ . : - and does not start with '/'")
            continue
        if nid in seen_nodes:
            c.add(f"{p}.id", f"duplicate node id '{nid}'")
            continue
        seen_nodes.add(nid)
        node_ids.append(nid)
    # ---- edges
    edges: list[dict] = []
    edges_raw = raw.get("edges")
    if "edges" not in raw:
        c.add("$.edges", "required")
        edges_raw = []
    elif not isinstance(edges_raw, list) or not edges_raw:
        c.add("$.edges", "must be a non-empty list")
        edges_raw = []
    if len(edges_raw) > limits["dependency_edges"]:
        c.add("$.edges", f"more than {limits['dependency_edges']} edges")
        edges_raw = []
    seen_edges: set[str] = set()
    for i, e in enumerate(edges_raw):
        p = f"$.edges[{i}]"
        if not isinstance(e, dict):
            c.add(p, "must be a mapping")
            continue
        _check_keys(e, EDGE_KEYS, p, c)
        for req in ("id", "from", "to", "relation"):
            if req not in e:
                c.add(f"{p}.{req}", "required")
        eid = _text(e["id"], f"{p}.id", c, sl, "edge id") if "id" in e else None
        if eid is not None:
            if not _ID_RE.fullmatch(eid):
                c.add(f"{p}.id", "an edge id uses only letters, digits and _ . : -")
                eid = None
            elif eid in seen_edges:
                c.add(f"{p}.id", f"duplicate edge id '{eid}'")
                eid = None
            else:
                seen_edges.add(eid)
        ends: list[str | None] = []
        for k in ("from", "to"):
            val = _text(e[k], f"{p}.{k}", c, sl, f"edge {k}") if k in e else None
            if val is not None:
                if val.startswith("/"):
                    if not _TOPIC_RE.fullmatch(val):
                        c.add(f"{p}.{k}", "a topic uses only letters, digits and _ / . ~ -")
                        val = None
                elif val not in seen_nodes:
                    c.add(f"{p}.{k}", f"'{val}' is neither a topic (starting with '/') nor a declared node")
                    val = None
            ends.append(val)
        if ends[0] is not None and ends[0] == ends[1]:
            c.add(p, "an edge from an endpoint to itself is not allowed")
            ends = [None, None]
        rel = e.get("relation")
        if "relation" in e and (not isinstance(rel, str) or rel not in EDGE_RELATIONS):
            c.add(f"{p}.relation", f"must be one of {list(EDGE_RELATIONS)}")
            rel = None
        win = None
        if "window_ns" in e:
            w = e["window_ns"]
            if not _is_int(w) or w <= 0:
                c.add(f"{p}.window_ns", "must be a positive integer number of nanoseconds")
                win = -1
            else:
                win = w
        if eid is not None and ends[0] is not None and ends[1] is not None and isinstance(rel, str) and win != -1:
            edges.append({"id": eid, "from": ends[0], "to": ends[1], "relation": rel, "window_ns": win})
    # ---- bindings
    bindings: list[dict] = []
    br = raw.get("bindings", [])
    if not isinstance(br, list):
        c.add("$.bindings", "must be a list")
        br = []
    if len(br) > limits["dependency_nodes"]:
        c.add("$.bindings", f"more than {limits['dependency_nodes']} bindings")
        br = []
    seen_b: set[tuple] = set()
    for i, b in enumerate(br):
        p = f"$.bindings[{i}]"
        if not isinstance(b, dict):
            c.add(p, "must be a mapping")
            continue
        _check_keys(b, BINDING_KEYS, p, c)
        for req in BINDING_KEYS:
            if req not in b:
                c.add(f"{p}.{req}", "required")
        node = b.get("node")
        if "node" in b and (not isinstance(node, str) or node not in seen_nodes):
            c.add(f"{p}.node", "must name a declared node")
            node = None
        topic = _text(b["topic"], f"{p}.topic", c, sl, "topic") if "topic" in b else None
        if topic is not None and not _TOPIC_RE.fullmatch(topic):
            c.add(f"{p}.topic", "a binding topic starts with '/' and uses only letters, digits and _ / . ~ -")
            topic = None
        ident = b.get("identity")
        pairs: list[tuple[str, str]] = []
        if "identity" in b:
            if not isinstance(ident, dict) or not ident:
                c.add(f"{p}.identity", "must be a non-empty mapping")
            else:
                _check_keys(ident, IDENTITY_KEYS, f"{p}.identity", c)
                for k in sorted(ident, key=str):
                    if isinstance(k, str) and k in IDENTITY_KEYS:
                        val = _text(ident[k], f"{p}.identity.{k}", c, sl, "identity value")
                        if val is not None and val == "":
                            c.add(f"{p}.identity.{k}", "identity value must not be empty")
                        elif val is not None:
                            pairs.append((k, val))
        if node is not None and topic is not None and pairs and len(pairs) == len(ident):
            key = (topic, tuple(pairs))
            if key in seen_b:
                c.add(p, "duplicate binding (same topic and identity)")
            else:
                seen_b.add(key)
                bindings.append({"node": node, "topic": topic, "identity": pairs})
    if c.total:
        return None
    return {"declared_by": declared_by, "nodes": sorted(node_ids), "bindings": bindings, "edges": sorted(edges, key=lambda e: e["id"])}


def _semantic_hash(content: dict) -> str:
    canon = {"dependency_schema_version": SCHEMA_VERSION, "declared_by": content["declared_by"], "nodes": content["nodes"],
             "bindings": sorted(({"node": b["node"], "topic": b["topic"], "identity": [list(p) for p in b["identity"]]} for b in content["bindings"]),
                                key=lambda b: (b["topic"], b["identity"], b["node"])),
             "edges": content["edges"]}
    return hashlib.sha256(json.dumps(canon, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def build_graph(content: dict, sha256: str) -> DependencyGraph:
    edges = tuple(Edge(e["id"], e["from"], e["to"], e["relation"], e["window_ns"]) for e in content["edges"])
    bindings = tuple(Binding(b["node"], b["topic"], tuple(sorted(tuple(p) for p in b["identity"]))) for b in content["bindings"])
    g = DependencyGraph(declared_by=content["declared_by"], nodes=tuple(content["nodes"]), bindings=bindings, edges=edges, sha256=sha256,
                        semantic_hash=_semantic_hash(content))
    for e in edges:
        g.out.setdefault(e.src, []).append(e)
        g.inc.setdefault(e.dst, []).append(e)
    for lst in g.out.values():
        lst.sort(key=lambda e: (e.dst, e.id))
    return g


def _fail(problems: list[DepProblem], sha: str | None = None, total: int | None = None) -> LoadedDependencies:
    return LoadedDependencies(None, problems, sha, total if total is not None else len(problems))


def parse_dependencies(data: bytes, *, name: str = "", limits: dict | None = None) -> LoadedDependencies:
    """Parse and validate the bytes of a dependency file. Never raises."""
    lim = {**DEFAULT_LIMITS, **(limits or {})}
    sha = hashlib.sha256(data).hexdigest()
    try:
        if len(data) > lim["dependency_file_bytes"]:
            return _fail([DepProblem("$", f"file is larger than {lim['dependency_file_bytes']} bytes")], sha)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return _fail([DepProblem("$", "file is not valid UTF-8 text")], sha)
        if "\x00" in text:
            return _fail([DepProblem("$", "file contains NUL characters")], sha)
        is_json = name.lower().endswith(".json")
        if is_json:
            msg = _scan_json(text, MAX_NESTING)
            if msg:
                return _fail([DepProblem("$", msg)], sha)
            try:
                raw = json.loads(text, object_pairs_hook=_json_pairs, parse_constant=_no_const)
            except (ValueError, RecursionError) as e:
                return _fail([DepProblem("$", f"JSON error: {str(e).splitlines()[0] if str(e) else 'invalid'}")], sha)
        else:
            msg = _scan_yaml(text, MAX_NESTING)
            if msg:
                return _fail([DepProblem("$", msg)], sha)
            try:
                raw = yaml.safe_load(text)
            except (yaml.YAMLError, RecursionError) as e:
                return _fail([DepProblem("$", f"YAML error: {str(e).splitlines()[0] if str(e) else 'invalid'}")], sha)
        if raw is None:
            return _fail([DepProblem("$", "the file is empty")], sha)
        c = _Collector()
        content = _validate(raw, lim, c)
        if content is None:
            return _fail(c.items, sha, c.total)
        return LoadedDependencies(build_graph(content, sha), [], sha, 0)
    except Exception as e:                                           # defensive: a loader must never raise on hostile input
        return _fail([DepProblem("$", f"unexpected {type(e).__name__} while reading the file")], sha)


def load_dependencies(path: str | Path, *, limits: dict | None = None) -> LoadedDependencies:
    """Read and validate a dependency file. Refuses anything that is not a regular, non-symlink file before opening it (no blocking on a FIFO)."""
    lim = {**DEFAULT_LIMITS, **(limits or {})}
    p = Path(path)
    try:
        st = os.lstat(p)
    except OSError as e:
        return _fail([DepProblem("$", f"cannot read the dependency file: {type(e).__name__}")])
    if stat.S_ISLNK(st.st_mode):
        return _fail([DepProblem("$", "the dependency file is a symbolic link; refusing")])
    if not stat.S_ISREG(st.st_mode):
        return _fail([DepProblem("$", "the dependency file is not a regular file")])
    if st.st_size > lim["dependency_file_bytes"]:
        return _fail([DepProblem("$", f"file is larger than {lim['dependency_file_bytes']} bytes")])
    try:
        with open(p, "rb") as fh:
            data = fh.read(lim["dependency_file_bytes"] + 1)
    except OSError as e:
        return _fail([DepProblem("$", f"cannot read the dependency file: {type(e).__name__}")])
    return parse_dependencies(data, name=p.name, limits=lim)
