"""Declared-dependency consistency, links and components (Step 6; DESIGN 4.4, 4.5 first bullet, FR-CON-01..04, FR-DEP-06).

A link relates a symptom X on node U with a symptom Y on node V in ONE lane, along ONE declared path U -> V. Its label says only whether the observed onset
intervals fit the declared edge's direction and window. A consistent link is temporal consistency with a person's declaration; it is never evidence of
causation. Pairs without a declared path are never linked or enumerated: they are only counted (arithmetically). Nothing here infers an edge."""
from __future__ import annotations

import hashlib
from bisect import bisect_right
from itertools import islice, product
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional

from pydantic import StrictBool, StrictInt, StrictStr, field_validator, model_validator

from ..anomaly.findings import AnomalyReport
from .blockers import ArtifactDigest, Blocker, SourceRecord, make_blocker
from .dependencies import DependencyGraph, Edge, WorkBudget, enumerate_paths
from .intervals import Interval
from .report import DeclaredStatement, DerivedOrderStatement, IntervalModel, ObservedStatement, Strict, canonical_json
from .symptoms import Symptom, canonical_key
from .vocabulary import CHAIN_DEFENCE, DEFAULT_LIMITS, LINK_LABELS, NODE_COVERAGE, PATH_KINDS, RULE_IDS

COVERING_STATUSES = ("findings_reported", "no_anomaly_observed")      # a detector that actually ran over its topics
BINDING_STATUSES = ("bound", "topic", "ambiguous")
WINDOW_RESULTS = ("within", "outside", "no_window", "not_applicable")
EDGE_COVERAGE = ("endpoints_have_symptoms", "no_symptom_on_endpoint")
LINK_NOTES = ("declared_edge_unverified", "path_via_unobserved_nodes")


# ------------------------------------------------------------------------------------------------------------------ the label

def classify(x: Interval, y: Interval, window_ns: Optional[int]) -> tuple[str, int, int]:
    """DESIGN 4.4 truth table for symptom X (upstream) and Y (downstream) -> (label, sep_min, sep_max); sep_min = Y.lower - X.upper."""
    sep_min, sep_max = x.min_separation(y), x.max_separation(y)
    if x.precedes(y):
        if window_ns is None or sep_min <= window_ns:
            return "consistent_with_propagation", sep_min, sep_max
        return "outside_declared_window", sep_min, sep_max
    if y.precedes(x):
        return "inconsistent_order", sep_min, sep_max
    return "undetermined", sep_min, sep_max


def path_window(edges: Iterable[Edge]) -> Optional[int]:
    """Sum of the edge windows iff EVERY edge declares one (FR-CON-02), else None."""
    total = 0
    for e in edges:
        if e.window_ns is None:
            return None
        total += e.window_ns
    return total


# ------------------------------------------------------------------------------------------------------------------ models

class NodeAssignment(Strict):
    symptom_id: StrictStr
    topic: StrictStr
    node: StrictStr
    binding: StrictStr

    @field_validator("binding")
    @classmethod
    def _b(cls, v: str) -> str:
        if v not in BINDING_STATUSES:
            raise ValueError(f"binding must be one of {BINDING_STATUSES}")
        return v


class IntermediateNode(Strict):
    node: StrictStr
    symptom_ids_between: list[StrictStr]
    coverage: StrictStr
    covering_detectors: list[StrictStr]

    @field_validator("coverage")
    @classmethod
    def _c(cls, v: str) -> str:
        if v not in NODE_COVERAGE:
            raise ValueError(f"coverage must be one of {NODE_COVERAGE}")
        return v


class WindowCheck(Strict):
    rule_id: StrictStr
    window_ns: Optional[StrictInt] = None
    sep_min_ns: StrictInt
    result: StrictStr

    @field_validator("rule_id")
    @classmethod
    def _r(cls, v: str) -> str:
        if v not in RULE_IDS:
            raise ValueError(f"rule_id must be one of {RULE_IDS}")
        return v

    @field_validator("result")
    @classmethod
    def _x(cls, v: str) -> str:
        if v not in WINDOW_RESULTS:
            raise ValueError(f"result must be one of {WINDOW_RESULTS}")
        return v


class Link(Strict):
    link_id: StrictStr
    lane: StrictStr
    from_symptom: StrictStr
    to_symptom: StrictStr
    from_node: StrictStr
    to_node: StrictStr
    path_kind: StrictStr
    edge_ids: list[StrictStr]
    window_ns: Optional[StrictInt] = None
    label: StrictStr
    from_onset: IntervalModel
    to_onset: IntervalModel
    sep_min_ns: StrictInt
    sep_max_ns: StrictInt
    order: DerivedOrderStatement
    window_check: WindowCheck
    observed: list[ObservedStatement]
    declared: list[DeclaredStatement]
    intermediates: list[IntermediateNode] = []
    notes: list[StrictStr] = []

    @field_validator("label")
    @classmethod
    def _l(cls, v: str) -> str:
        if v not in LINK_LABELS:
            raise ValueError(f"label must be one of {LINK_LABELS}")
        return v

    @field_validator("path_kind")
    @classmethod
    def _p(cls, v: str) -> str:
        if v not in PATH_KINDS:
            raise ValueError(f"path_kind must be one of {PATH_KINDS}")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> "Link":
        x, y = self.from_onset.to_interval(), self.to_onset.to_interval()        # raises on a clock-domain mismatch
        if x.domain != self.lane:
            raise ValueError("a link lives in one lane: both onsets must be in it")
        label, smin, smax = classify(x, y, self.window_ns)
        if (label, smin, smax) != (self.label, self.sep_min_ns, self.sep_max_ns):
            raise ValueError("label and separations must follow the declared truth table")
        if not self.edge_ids or (self.path_kind == "direct") != (len(self.edge_ids) == 1):
            raise ValueError("path_kind must match the number of edges")
        if len(self.declared) != len(self.edge_ids) or [d.edge_id for d in self.declared] != self.edge_ids:
            raise ValueError("every edge of the path must be cited by a declared statement")
        if [o.source_id for o in self.observed] != [self.from_symptom, self.to_symptom]:
            raise ValueError("observed statements must cite the two symptoms")
        if (self.window_check.window_ns, self.window_check.sep_min_ns) != (self.window_ns, self.sep_min_ns):
            raise ValueError("window check must repeat the link's window and separation")
        expect = {"consistent_with_propagation": "no_window" if self.window_ns is None else "within", "outside_declared_window": "outside"}.get(self.label, "not_applicable")
        if self.window_check.result != expect:
            raise ValueError("window check result does not match the label")
        want_rel = {"consistent_with_propagation": "before", "outside_declared_window": "before", "inconsistent_order": "before", "undetermined": "unordered"}[self.label]
        first, second = (self.to_onset, self.from_onset) if self.label == "inconsistent_order" else (self.from_onset, self.to_onset)
        if (self.order.relation, self.order.earlier, self.order.later, self.order.rule_id) != (want_rel, first, second, "interval_order.strict_precedence"):
            raise ValueError("the order statement must show the two intervals in their derived order")
        if self.intermediates and self.path_kind != "via_nodes":
            raise ValueError("only a via_nodes link has intermediate nodes")
        if self.path_kind == "via_nodes" and len(self.intermediates) != len(self.edge_ids) - 1:
            raise ValueError("a via_nodes link reports every intermediate node")
        if any(n not in LINK_NOTES for n in self.notes) or "declared_edge_unverified" not in self.notes:
            raise ValueError(f"notes are drawn from {LINK_NOTES} and always include declared_edge_unverified")
        return self


class EdgeCoverage(Strict):
    edge_id: StrictStr
    from_node: StrictStr
    to_node: StrictStr
    from_symptom_count: StrictInt
    to_symptom_count: StrictInt
    status: StrictStr
    from_coverage: StrictStr
    to_coverage: StrictStr

    @field_validator("status")
    @classmethod
    def _s(cls, v: str) -> str:
        if v not in EDGE_COVERAGE:
            raise ValueError(f"status must be one of {EDGE_COVERAGE}")
        return v


class Component(Strict):
    component_id: StrictStr
    symptom_ids: list[StrictStr]
    lanes: list[StrictStr]
    link_ids: list[StrictStr]
    defence: StrictStr
    blocker_ids: list[StrictStr] = []

    @field_validator("defence")
    @classmethod
    def _d(cls, v: str) -> str:
        if v not in CHAIN_DEFENCE:
            raise ValueError(f"defence must be one of {CHAIN_DEFENCE}")
        return v

    @model_validator(mode="after")
    def _c(self) -> "Component":
        if not self.symptom_ids or self.symptom_ids != sorted(set(self.symptom_ids)):
            raise ValueError("a component has at least one symptom, listed once and sorted")
        if self.defence == "defended_structure" and self.blocker_ids:
            raise ValueError("a defended structure has no blockers")
        return self


class ConsistencyResult(Strict):
    assignments: list[NodeAssignment]
    links: list[Link]
    links_total: StrictInt                       # EXACT number of links over the paths that were enumerated
    links_suppressed: StrictInt                  # exact: links_total - len(links)
    links_status: StrictStr                      # complete | capped | bounded_paths
    path_pairs_related: StrictInt                # node pairs with a declared path (exact, by reachability)
    path_pairs_examined: StrictInt
    path_pairs_not_examined: StrictInt
    no_declared_relation_count: StrictInt        # per lane: same-lane pairs of symptoms on different nodes with no declared path either way
    cross_lane_pairs_not_compared: StrictInt     # symptom pairs on declared-path nodes that share no lane
    edge_coverage: list[EdgeCoverage]
    components: list[Component] = []

    @model_validator(mode="after")
    def _x(self) -> "ConsistencyResult":
        if self.links_total != len(self.links) + self.links_suppressed or self.links_suppressed < 0:
            raise ValueError("links_total must equal reported plus suppressed links")
        if self.links_status not in ("complete", "capped", "bounded_paths"):
            raise ValueError("unknown links_status")
        return self


# ------------------------------------------------------------------------------------------------------------------ coverage

def detector_coverage(report: AnomalyReport, graph: DependencyGraph) -> dict[str, tuple[str, list[str]]]:
    """endpoint -> (coverage, detectors). `covered` iff the endpoint has topics and every one of them is covered by a detector that ran;
    the detector list is every such detector covering at least one of them. A node nobody looked at is `not_covered`, never 'not affected'."""
    covering: dict[str, set[str]] = {}
    for r in report.results:
        if r.status in COVERING_STATUSES:
            for t in r.topics:
                covering.setdefault(t, set()).add(r.detector_id)
    node_topics: dict[str, set[str]] = {}
    for b in graph.bindings:
        node_topics.setdefault(b.node, set()).add(b.topic)
    out: dict[str, tuple[str, list[str]]] = {}
    for ep in graph.endpoints:
        topics = {ep} if ep.startswith("/") else node_topics.get(ep, set())
        dets = sorted({d for t in topics for d in covering.get(t, ())})
        ok = bool(topics) and all(t in covering for t in topics)
        out[ep] = ("covered" if ok else "not_covered", dets)
    return out


# ------------------------------------------------------------------------------------------------------------------ analysis

@dataclass
class _Ctx:
    graph: DependencyGraph
    by_node_lane: dict[tuple[str, str], list[Symptom]]
    lowers: dict[tuple[str, str], list[int]]
    coverage: dict[str, tuple[str, list[str]]]
    symptoms: dict[str, Symptom]


def assign_nodes(symptoms: Iterable[Symptom], graph: DependencyGraph) -> list[NodeAssignment]:
    out = []
    for s in sorted(symptoms, key=canonical_key):
        node, status = graph.node_for(s.topic, s.identities)
        out.append(NodeAssignment(symptom_id=s.symptom_id, topic=s.topic, node=node, binding=status))
    return out


def apply_assignments(symptoms: Iterable[Symptom], assignments: Iterable[NodeAssignment]) -> list[Symptom]:
    node = {a.symptom_id: a.node for a in assignments}
    return [s.model_copy(update={"node": node[s.symptom_id]}) for s in symptoms]


def _descendants(graph: DependencyGraph, src: str) -> set[str]:
    seen: set[str] = set()
    stack = [src]
    while stack:
        cur = stack.pop()
        for e in graph.out.get(cur, ()):
            if e.dst not in seen:
                seen.add(e.dst)
                stack.append(e.dst)
    return seen


def _between(ctx: _Ctx, node: str, lane: str, x: Interval, y: Interval) -> list[str]:
    """Symptoms on `node` in `lane` strictly after X and strictly before Y (X precedes Z and Z precedes Y)."""
    lst = ctx.by_node_lane.get((node, lane), [])
    lows = ctx.lowers.get((node, lane), [])
    out = []
    for i in range(bisect_right(lows, x.upper_ns), len(lst)):
        iv = lst[i].onset_in(lane)
        if iv.lower_ns >= y.lower_ns:
            break
        if iv.upper_ns < y.lower_ns:
            out.append(lst[i].symptom_id)
    return sorted(out)


def _make_link(ctx: _Ctx, path: tuple[Edge, ...], lane: str, xs: Symptom, ys: Symptom, window: Optional[int]) -> Link:
    x, y = xs.onset_in(lane), ys.onset_in(lane)
    label, smin, smax = classify(x, y, window)
    xm, ym = IntervalModel.of(x), IntervalModel.of(y)
    first, second = (ym, xm) if label == "inconsistent_order" else (xm, ym)
    rel = "unordered" if label == "undetermined" else "before"
    inter: list[IntermediateNode] = []
    for e in path[:-1]:
        cov, dets = ctx.coverage.get(e.dst, ("not_covered", []))
        inter.append(IntermediateNode(node=e.dst, symptom_ids_between=_between(ctx, e.dst, lane, x, y), coverage=cov, covering_detectors=dets))
    notes = ["declared_edge_unverified"] + (["path_via_unobserved_nodes"] if any(not n.symptom_ids_between for n in inter) else [])
    g = ctx.graph
    edge_ids = [e.id for e in path]
    ident = {"from": xs.symptom_id, "to": ys.symptom_id, "lane": lane, "edges": edge_ids}
    return Link(
        link_id="l-" + hashlib.sha256(canonical_json(ident).encode("utf-8")).hexdigest()[:16], lane=lane, from_symptom=xs.symptom_id, to_symptom=ys.symptom_id,
        from_node=xs.node, to_node=ys.node, path_kind="direct" if len(path) == 1 else "via_nodes", edge_ids=edge_ids, window_ns=window, label=label,
        from_onset=xm, to_onset=ym, sep_min_ns=smin, sep_max_ns=smax,
        order=DerivedOrderStatement(rule_id="interval_order.strict_precedence", earlier=first, later=second, relation=rel),
        window_check=WindowCheck(rule_id="edge_consistency.declared_window", window_ns=window, sep_min_ns=smin,
                                 result={"consistent_with_propagation": "no_window" if window is None else "within", "outside_declared_window": "outside"}.get(label, "not_applicable")),
        observed=[ObservedStatement(source_kind=xs.source, source_id=xs.symptom_id), ObservedStatement(source_kind=ys.source, source_id=ys.symptom_id)],
        declared=[DeclaredStatement(dependencies_sha256=g.sha256, semantic_hash=g.semantic_hash, edge_id=e.id, declared_by=g.declared_by) for e in path],
        intermediates=inter, notes=notes)


def analyse_links(symptoms: list[Symptom], graph: DependencyGraph, report: AnomalyReport, *, limits: dict | None = None,
                  coverage: dict[str, tuple[str, list[str]]] | None = None, budget: WorkBudget | None = None) -> tuple[ConsistencyResult, list[Blocker]]:
    """Links, counters and the blockers raised by the link / path limits. `symptoms` are used with the node given by the declared bindings."""
    lim = {**DEFAULT_LIMITS, **(limits or {})}
    assigns = assign_nodes(symptoms, graph)
    syms = apply_assignments(symptoms, assigns)
    by_node: dict[str, list[Symptom]] = {}
    by_node_lane: dict[tuple[str, str], list[Symptom]] = {}
    for s in syms:
        by_node.setdefault(s.node, []).append(s)
        for lane in s.lanes:
            by_node_lane.setdefault((s.node, lane), []).append(s)
    lowers: dict[tuple[str, str], list[int]] = {}
    for (n, lane), lst in by_node_lane.items():
        lst.sort(key=lambda s: (s.onset_in(lane).lower_ns, s.onset_in(lane).upper_ns, s.symptom_id))
        lowers[(n, lane)] = [s.onset_in(lane).lower_ns for s in lst]
    cov = coverage if coverage is not None else detector_coverage(report, graph)
    ctx = _Ctx(graph, by_node_lane, lowers, cov, {s.symptom_id: s for s in syms})
    in_graph = set(graph.endpoints)
    bearing = sorted(n for n in by_node if n in in_graph)

    budget = budget if budget is not None else WorkBudget(int(lim["path_work_limit"]))   # one budget per run: chains share it (clarification B)
    cap = int(lim["links"])
    links: list[Link] = []
    total = 0
    related = examined = 0
    pair_cap_pairs = depth_pairs = 0
    work_hit = False
    # counters are accumulated while the pairs are visited (no per-pair set is kept: memory stays proportional to the symptoms, not to the node pairs)
    lane_nodes: dict[str, dict[str, int]] = {}
    for (n, lane), lst in by_node_lane.items():
        lane_nodes.setdefault(lane, {})[n] = len(lst)
    lane_list = sorted(lane_nodes)
    rel_pairs = {ln: 0 for ln in lane_list}                  # per lane: same-lane symptom pairs on declared-path nodes (unordered node pairs counted once)
    groups: dict[str, dict[frozenset, int]] = {}             # node -> {lane set: number of its symptoms with exactly that lane set}
    for n, lst in by_node.items():
        g = groups.setdefault(n, {})
        for sy in lst:
            k = frozenset(sy.lanes)
            g[k] = g.get(k, 0) + 1
    cross = 0
    bearing_set = set(bearing)
    for u in bearing:
        targets = sorted(_descendants(graph, u) & bearing_set - {u})
        reach_u = graph.reaches(u)                           # endpoints that reach u: a target in it is related both ways
        gu = groups[u]
        for v in targets:
            related += 1
            if not (v < u and v in reach_u):                 # an unordered related pair is counted once
                for ln in lane_list:
                    rel_pairs[ln] += lane_nodes[ln].get(u, 0) * lane_nodes[ln].get(v, 0)
            cross += sum(a * b for ku, a in gu.items() for kv, b in groups[v].items() if not (ku & kv))
            if work_hit or budget.exhausted:
                work_hit = True
                continue
            pr = enumerate_paths(graph, u, v, max_depth=int(lim["path_depth"]), max_paths=int(lim["paths_per_pair"]), budget=budget)
            if pr.work_exhausted:
                work_hit = True
                continue                                          # a partially searched pair is counted as not examined, its paths are not used
            examined += 1
            pair_cap_pairs += pr.pair_cap_reached
            depth_pairs += pr.depth_truncated
            lanes = sorted({ln for s in by_node[u] for ln in s.lanes} & {ln for s in by_node[v] for ln in s.lanes})
            for path in pr.paths:
                w = path_window(path)
                for lane in lanes:
                    xs, ys = by_node_lane.get((u, lane), []), by_node_lane.get((v, lane), [])
                    total += len(xs) * len(ys)
                    for x, y in islice(product(xs, ys), max(0, cap - len(links))):
                        links.append(_make_link(ctx, path, lane, x, y, w))
    suppressed = total - len(links)

    # counters (arithmetic, no pair is enumerated)
    no_decl = 0
    for lane, cn in lane_nodes.items():
        s1 = sum(cn.values())
        diff_node_pairs = (s1 * s1 - sum(c * c for c in cn.values())) // 2
        no_decl += diff_node_pairs - rel_pairs[lane]

    ecov = []
    for e in graph.edges:
        a, b = len(by_node.get(e.src, [])), len(by_node.get(e.dst, []))
        ecov.append(EdgeCoverage(edge_id=e.id, from_node=e.src, to_node=e.dst, from_symptom_count=a, to_symptom_count=b,
                                 status="endpoints_have_symptoms" if a and b else "no_symptom_on_endpoint",
                                 from_coverage=cov.get(e.src, ("not_covered", []))[0], to_coverage=cov.get(e.dst, ("not_covered", []))[0]))

    status = "bounded_paths" if (work_hit or pair_cap_pairs or depth_pairs) else ("capped" if suppressed else "complete")
    dep = [ArtifactDigest(artifact_ref="dependencies", sha256=graph.sha256)]
    blk: list[Blocker] = []
    if suppressed:
        blk.append(make_blocker("link_cap_reached", SourceRecord(kind="m3d_limit", ref="links", field="limits.links"), count=suppressed, artifacts=dep))
    if pair_cap_pairs:
        blk.append(make_blocker("link_cap_reached", SourceRecord(kind="m3d_limit", ref="paths", field="limits.paths_per_pair"), count=pair_cap_pairs, artifacts=dep))
    if depth_pairs:
        blk.append(make_blocker("link_cap_reached", SourceRecord(kind="m3d_limit", ref="paths", field="limits.path_depth"), count=depth_pairs, artifacts=dep))
    if work_hit:
        blk.append(make_blocker("path_work_limit_reached", SourceRecord(kind="m3d_limit", ref="paths", field="limits.path_work_limit"), count=related - examined, artifacts=dep))
    res = ConsistencyResult(assignments=assigns, links=links, links_total=total, links_suppressed=suppressed, links_status=status, path_pairs_related=related,
                            path_pairs_examined=examined, path_pairs_not_examined=related - examined, no_declared_relation_count=no_decl,
                            cross_lane_pairs_not_compared=cross, edge_coverage=ecov)
    return res, sorted(blk, key=lambda b: b.blocker_id)


# ------------------------------------------------------------------------------------------------------------------ components

def components(symptoms: Iterable[Symptom], links: Iterable[Link], *, links_incomplete: bool = False,
               blockers_by_lane: dict[str, list[str]] | None = None) -> list[Component]:
    """Weakly connected components of the `consistent_with_propagation` link graph over ALL symptoms (a symptom without such a link is a singleton
    component; FR-CHN-01). Parameter-free; union-find, iterative. A component is `not_defended` when the link list is incomplete, a member is truncated, or a
    blocker applies to a lane of one of its links (a singleton: to one of its own lanes)."""
    sym = {s.symptom_id: s for s in symptoms}
    parent: dict[str, str] = {a: a for a in sym}

    def find(a: str) -> str:
        root = a
        while parent[root] != root:
            root = parent[root]
        while parent[a] != root:
            parent[a], a = root, parent[a]
        return root

    used = [l for l in links if l.label == "consistent_with_propagation"]
    for l in used:
        for a in (l.from_symptom, l.to_symptom):
            parent.setdefault(a, a)
        ra, rb = find(l.from_symptom), find(l.to_symptom)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    groups: dict[str, list[str]] = {}
    for a in parent:
        groups.setdefault(find(a), []).append(a)
    link_by_root: dict[str, list[Link]] = {}
    for l in used:
        link_by_root.setdefault(find(l.from_symptom), []).append(l)
    out = []
    bbl = blockers_by_lane or {}
    for root, members in groups.items():
        ls = link_by_root.get(root, [])
        lanes = sorted({l.lane for l in ls} if ls else {ln for m in members if m in sym for ln in sym[m].lanes})
        bl = sorted({b for lane in lanes for b in bbl.get(lane, [])})
        bad = links_incomplete or bool(bl) or any(sym[m].truncated for m in members if m in sym)
        ids = sorted(members)
        out.append(Component(component_id="c-" + hashlib.sha256(canonical_json(ids).encode("utf-8")).hexdigest()[:16], symptom_ids=ids, lanes=lanes,
                             link_ids=sorted(l.link_id for l in ls), defence="not_defended" if bad else "defended_structure", blocker_ids=bl))
    out.sort(key=lambda c: (c.symptom_ids[0], c.component_id))
    return out
