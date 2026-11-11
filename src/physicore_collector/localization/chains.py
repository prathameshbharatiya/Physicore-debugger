"""Chains, hypotheses and localization (Step 7; DESIGN 4.5, 4.6, FR-CHN-01..07, clarification B).

A chain is a maximal directed path of the link DAG of ONE lane (lanes are never mixed: order across clock domains is never compared). Its first symptom is
the `head`. Chains are enumerated lazily in a deterministic order and only the first `chains_reported` are kept; the total is computed by dynamic programming
over the DAG (never by materialising chains) and is reported only when exact; the shared work budget bounds all candidate-path processing. Exactly three
hypothesis templates exist; none is ranked. Localization states where symptoms were OBSERVED, never where a fault is. Nothing here is recursive."""
from __future__ import annotations

import hashlib
from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional

from pydantic import StrictInt, StrictStr, field_validator, model_validator

from .blockers import CHAIN_LEVEL_CODES, ArtifactDigest, Blocker, Caveat, SourceRecord, blockers_by_lane as _blockers_by_lane, make_blocker
from .consistency import Component, ConsistencyResult, Link, components as _components
from .dependencies import DependencyGraph, WorkBudget
from .intervals import Interval
from .precedence import layer_map
from .report import IntervalModel, Strict, canonical_json
from .symptoms import Symptom
from .vocabulary import (CHAIN_DEFENCE, CHAINS_TOTAL_STATUS, DEFAULT_LIMITS, DISCRIMINATING_EVIDENCE, HYPOTHESIS_SENTENCES, HYPOTHESIS_STATUSES,
                         HYPOTHESIS_TEMPLATES, NODE_COVERAGE)

T_LINK, T_COMMON, T_ARTIFACT = HYPOTHESIS_TEMPLATES
ANCESTORS_LISTED = 50                 # fixed output bounds; the exact totals are always reported next to the truncated lists
SYMPTOMS_LISTED_PER_ANCESTOR = 20
LINK_STATUS = {"consistent_with_propagation": "consistent_with", "outside_declared_window": "excluded_by_declared_edge_timing",
               "inconsistent_order": "excluded_by_declared_edge_timing", "undetermined": "not_excluded"}


# ------------------------------------------------------------------------------------------------------------------ the link DAG

@dataclass
class LaneDag:
    lane: str
    key: dict[str, tuple[int, int, str]]                 # vertex -> (upper, lower, id) in this lane; ascending `upper` is a topological order
    succ: dict[str, list[str]]                           # successors in canonical order
    arc_links: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    pred_count: dict[str, int] = field(default_factory=dict)

    @property
    def vertices(self) -> list[str]:
        return sorted(self.key, key=lambda v: (self.key[v][1], self.key[v][0], v))

    @property
    def sources(self) -> list[str]:
        return [v for v in self.vertices if self.pred_count.get(v, 0) == 0]

    @property
    def arc_count(self) -> int:
        return sum(len(v) for v in self.succ.values())


def build_dags(links: Iterable[Link]) -> list[LaneDag]:
    """One DAG per lane from the `consistent_with_propagation` links. Parallel links (several declared paths) merge into one arc that lists them all.
    A `via_nodes` link X -> Y is left out when its own declared path is already walked by consistent arcs through an OBSERVED intermediate symptom Z
    (X -> Z and Z -> Y both exist): the longer chain says more. A link whose intermediates are unobserved stays, so a linear A -> B -> C is one chain."""
    per_lane: dict[str, list[Link]] = {}
    for l in sorted(links, key=lambda l: l.link_id):
        if l.label == "consistent_with_propagation":
            if l.from_onset.upper_ns >= l.to_onset.lower_ns:
                raise ValueError("a consistent link must be strictly forward in time; the link DAG would not be acyclic")
            per_lane.setdefault(l.lane, []).append(l)
    out = []
    for lane in sorted(per_lane):
        ls = per_lane[lane]
        all_arcs = {(l.from_symptom, l.to_symptom) for l in ls}
        d = LaneDag(lane, {}, {})
        for l in ls:
            if l.path_kind == "via_nodes" and any((l.from_symptom, z) in all_arcs and (z, l.to_symptom) in all_arcs
                                                  for n in l.intermediates for z in n.symptom_ids_between):
                continue
            for sid, iv in ((l.from_symptom, l.from_onset), (l.to_symptom, l.to_onset)):
                d.key[sid] = (iv.upper_ns, iv.lower_ns, sid)
            d.arc_links.setdefault((l.from_symptom, l.to_symptom), []).append(l.link_id)
        outs: dict[str, list[str]] = {}
        for (x, y) in d.arc_links:
            outs.setdefault(x, []).append(y)
            d.pred_count[y] = d.pred_count.get(y, 0) + 1
        for x, lst in outs.items():
            lst.sort(key=lambda v: (d.key[v][1], d.key[v][0], v))
        d.succ = outs
        if d.key:
            out.append(d)
    return out


def count_maximal_paths(dag: LaneDag) -> int:
    """Number of maximal paths (source to sink) by dynamic programming over the DAG, O(V + E), exact big integers. No path is materialised."""
    f: dict[str, int] = {}
    for v in sorted(dag.key, key=lambda v: dag.key[v], reverse=True):          # successors have a larger `upper`: they are done first
        outs = dag.succ.get(v)
        f[v] = sum(f[w] for w in outs) if outs else 1
    return sum(f[v] for v in dag.sources)


class _State:
    def __init__(self) -> None:
        self.done_heads: set[tuple[str, str]] = set()
        self.stopped: Optional[str] = None             # "work" when the shared budget ran out


def iter_chains(dags: list[LaneDag], budget: WorkBudget, state: _State) -> Iterator[tuple[str, tuple[str, ...]]]:
    """Maximal paths in a deterministic order (lanes by name, heads by canonical order, successors by canonical order), lazily, iteratively. Every
    vertex visit takes one unit of the shared work budget; when it is spent the generator stops and `state.stopped == 'work'`."""
    for dag in dags:
        for head in dag.sources:
            if not budget.take():
                state.stopped = "work"
                return
            path, idx = [head], [0]
            while path:
                v = path[-1]
                outs = dag.succ.get(v, ())
                i = idx[-1]
                if i >= len(outs):
                    path.pop()
                    idx.pop()
                    continue
                idx[-1] = i + 1
                w = outs[i]
                if not budget.take():
                    state.stopped = "work"
                    return
                path.append(w)
                idx.append(0)
                if w not in dag.succ:
                    yield dag.lane, tuple(path)
                    path.pop()
                    idx.pop()
            state.done_heads.add((dag.lane, head))


@dataclass
class Enumeration:
    kept: list[tuple[str, tuple[str, ...]]]
    total: Optional[int]                      # exact number of maximal paths, or None (bounded_unknown)
    truncated_by: Optional[str]               # report_limit | work_limit | None
    state: "_State"


def enumerate_chains(dags: list[LaneDag], budget: WorkBudget, limit: int, count_limit: int) -> Enumeration:
    """The deterministic first `limit` maximal paths (never the whole set) and the exact total when V + E fits in `count_limit` (DP, no enumeration)."""
    size = sum(len(d.key) + d.arc_count for d in dags)
    total: Optional[int] = sum(count_maximal_paths(d) for d in dags) if size <= count_limit else None
    state = _State()
    kept: list[tuple[str, tuple[str, ...]]] = []
    truncated_by: Optional[str] = None
    for item in iter_chains(dags, budget, state):
        if len(kept) >= limit:
            truncated_by = "report_limit"          # one more chain exists: this is how an exactly-full report is told from a truncated one
            break
        kept.append(item)
    if truncated_by is None and state.stopped == "work":
        truncated_by = "work_limit"
    if total is not None and truncated_by is None and len(kept) != total:
        raise AssertionError("internal: the DP total disagrees with a complete enumeration")
    return Enumeration(kept, total, truncated_by, state)


# ------------------------------------------------------------------------------------------------------------------ models

class ChainStep(Strict):
    from_symptom: StrictStr
    to_symptom: StrictStr
    link_ids: list[StrictStr]


class Ancestor(Strict):
    node: StrictStr
    symptom_count_before_head: StrictInt
    symptom_ids_before_head: list[StrictStr]
    coverage: StrictStr
    covering_detectors: list[StrictStr]

    @field_validator("coverage")
    @classmethod
    def _c(cls, v: str) -> str:
        if v not in NODE_COVERAGE:
            raise ValueError(f"coverage must be one of {NODE_COVERAGE}")
        return v


class CommonAncestorFacts(Strict):
    ancestors_total: StrictInt
    ancestors: list[Ancestor]

    @model_validator(mode="after")
    def _n(self) -> "CommonAncestorFacts":
        if len(self.ancestors) > min(self.ancestors_total, ANCESTORS_LISTED):
            raise ValueError("more ancestors listed than exist or than the output bound allows")
        return self


class ArtifactFacts(Strict):
    caveat_codes: list[StrictStr]
    recording_blocker_ids: list[StrictStr]


class Hypothesis(Strict):
    """Template 2 or 3 of a chain. Always `not_excluded`; no rank, score or order other than the template name."""
    template: StrictStr
    status: StrictStr
    statement: StrictStr
    common_ancestors: Optional[CommonAncestorFacts] = None
    artifact_facts: Optional[ArtifactFacts] = None
    discriminating_evidence_needed: list[StrictStr]

    @model_validator(mode="after")
    def _ok(self) -> "Hypothesis":
        if self.template not in (T_COMMON, T_ARTIFACT):
            raise ValueError(f"a chain carries only the templates {(T_COMMON, T_ARTIFACT)}")
        if self.status != "not_excluded":
            raise ValueError("templates 2 and 3 are always not_excluded")
        if self.statement != HYPOTHESIS_SENTENCES[(self.template, self.status)]:
            raise ValueError("the statement is the fixed sentence of the template")
        if (self.template == T_COMMON) != (self.common_ancestors is not None) or (self.template == T_ARTIFACT) != (self.artifact_facts is not None):
            raise ValueError("each template carries exactly its own facts")
        bad = [e for e in self.discriminating_evidence_needed if e not in DISCRIMINATING_EVIDENCE]
        if bad or self.discriminating_evidence_needed != [e for e in DISCRIMINATING_EVIDENCE if e in self.discriminating_evidence_needed]:
            raise ValueError("discriminating evidence comes from the fixed vocabulary, in vocabulary order")
        return self


class LinkHypothesis(Strict):
    """Template 1 for one link: the only exclusion M3d can state, relative to the declared edge's own direction and window, with both intervals shown."""
    link_id: StrictStr
    template: StrictStr = T_LINK
    status: StrictStr
    statement: StrictStr
    label: StrictStr
    edge_ids: list[StrictStr]
    window_ns: Optional[StrictInt] = None
    from_onset: IntervalModel
    to_onset: IntervalModel

    @model_validator(mode="after")
    def _ok(self) -> "LinkHypothesis":
        if self.template != T_LINK or self.status not in HYPOTHESIS_STATUSES:
            raise ValueError("template 1 with a status of the closed vocabulary")
        if LINK_STATUS.get(self.label) != self.status:
            raise ValueError("the status follows from the link label")
        if self.statement != HYPOTHESIS_SENTENCES[(T_LINK, self.status)]:
            raise ValueError("the statement is the fixed sentence of the template")
        return self


class Chain(Strict):
    chain_id: StrictStr
    lane: StrictStr
    head: StrictStr
    symptom_ids: list[StrictStr]
    nodes: list[StrictStr]
    steps: list[ChainStep]
    component_id: StrictStr
    defence: StrictStr
    blocker_ids: list[StrictStr]
    hypotheses: list[Hypothesis]

    @model_validator(mode="after")
    def _ok(self) -> "Chain":
        if self.defence not in CHAIN_DEFENCE:
            raise ValueError(f"defence must be one of {CHAIN_DEFENCE}")
        if len(self.symptom_ids) < 2 or self.head != self.symptom_ids[0] or len(set(self.symptom_ids)) != len(self.symptom_ids):
            raise ValueError("a chain has a head and at least one more distinct symptom")
        if [(s.from_symptom, s.to_symptom) for s in self.steps] != list(zip(self.symptom_ids, self.symptom_ids[1:])) or len(self.nodes) != len(self.symptom_ids):
            raise ValueError("steps and nodes follow the symptoms")
        if self.defence == "defended_structure" and self.blocker_ids:
            raise ValueError("a defended structure has no blockers")
        names = [h.template for h in self.hypotheses]
        if names != sorted(names):
            raise ValueError("hypotheses are ordered by template name only")
        return self


class LayerNodes(Strict):
    lane: StrictStr
    locus_nodes: list[StrictStr]
    frontier_nodes: list[StrictStr]


class ComponentLocalization(Strict):
    component_id: StrictStr
    lanes: list[LayerNodes]


class Unlocalized(Strict):
    symptom_id: StrictStr
    topic: StrictStr
    node: StrictStr


class TopicLayer(Strict):
    lane: StrictStr
    topic: StrictStr
    layer: StrictInt
    symptom_ids: list[StrictStr]


class Localization(Strict):
    components: list[ComponentLocalization]
    unlocalized: list[Unlocalized]
    topic_layers: list[TopicLayer]
    statement: StrictStr = "Where symptoms were observed; not where a fault is."


class ChainAnalysis(Strict):
    components: list[Component]
    chains: list[Chain]
    chains_reported: StrictInt
    chains_total: Optional[StrictInt] = None
    chains_total_status: StrictStr
    chains_lower_bound: StrictInt
    chains_truncated_by: Optional[StrictStr] = None           # report_limit | work_limit | None
    link_hypotheses: list[LinkHypothesis]
    localization: Localization

    @model_validator(mode="after")
    def _ok(self) -> "ChainAnalysis":
        if self.chains_total_status not in CHAINS_TOTAL_STATUS:
            raise ValueError(f"chains_total_status must be one of {CHAINS_TOTAL_STATUS}")
        if (self.chains_total_status == "exact") != (self.chains_total is not None):
            raise ValueError("chains_total is present exactly when it is exact; an estimate is never a total")
        if self.chains_reported != len(self.chains) or self.chains_lower_bound < self.chains_reported:
            raise ValueError("chains_reported and the lower bound must match the chain list")
        if self.chains_total is not None and self.chains_total < self.chains_reported:
            raise ValueError("the total cannot be below the reported count")
        if self.chains_truncated_by not in (None, "report_limit", "work_limit"):
            raise ValueError("unknown truncation reason")
        return self


# ------------------------------------------------------------------------------------------------------------------ hypotheses

def link_hypothesis(l: Link) -> LinkHypothesis:
    status = LINK_STATUS[l.label]
    return LinkHypothesis(link_id=l.link_id, status=status, statement=HYPOTHESIS_SENTENCES[(T_LINK, status)], label=l.label, edge_ids=l.edge_ids,
                          window_ns=l.window_ns, from_onset=l.from_onset, to_onset=l.to_onset)


def _evidence(*wanted: str) -> list[str]:
    return [e for e in DISCRIMINATING_EVIDENCE if e in wanted]


# ------------------------------------------------------------------------------------------------------------------ localization

def _localize(components: list[Component], by_id: dict[str, Symptom]) -> list[ComponentLocalization]:
    out = []
    for c in components:
        lanes = []
        for lane in c.lanes:
            members = [by_id[m] for m in c.symptom_ids if m in by_id and by_id[m].onset_in(lane) is not None]
            lm = layer_map((m.symptom_id, m.onset_in(lane)) for m in members)
            top = max(lm.values()) if lm else 0
            node = {m.symptom_id: m.node for m in members}
            lanes.append(LayerNodes(lane=lane, locus_nodes=sorted({node[s] for s, k in lm.items() if k == 0}),
                                    frontier_nodes=sorted({node[s] for s, k in lm.items() if k == top})))
        out.append(ComponentLocalization(component_id=c.component_id, lanes=lanes))
    return out


def topic_layers(symptoms: Iterable[Symptom]) -> list[TopicLayer]:
    """Without a dependency file: symptoms are localized to topics and time layers only."""
    per_lane: dict[str, list[Symptom]] = {}
    for s in symptoms:
        for lane in s.lanes:
            per_lane.setdefault(lane, []).append(s)
    out = []
    for lane in sorted(per_lane):
        lm = layer_map((s.symptom_id, s.onset_in(lane)) for s in per_lane[lane])
        groups: dict[tuple[str, int], list[str]] = {}
        for s in per_lane[lane]:
            groups.setdefault((s.topic, lm[s.symptom_id]), []).append(s.symptom_id)
        out += [TopicLayer(lane=lane, topic=t, layer=k, symptom_ids=sorted(v)) for (t, k), v in sorted(groups.items())]
    return out


# ------------------------------------------------------------------------------------------------------------------ the analysis

def analyse_chains(symptoms: list[Symptom], links: list[Link], consistency: ConsistencyResult, graph: DependencyGraph | None, *,
                   blockers: Iterable[Blocker] = (), caveats: Iterable[Caveat] = (), coverage: dict[str, tuple[str, list[str]]] | None = None,
                   budget: WorkBudget | None = None, limits: dict | None = None) -> tuple[ChainAnalysis, list[Blocker]]:
    """`symptoms` carry their declared node. `blockers` are all blockers raised so far (lane-scoped and chain-level). Returns the analysis and the blockers it
    raised itself (`chains_truncated`, `path_work_limit_reached`)."""
    lim = {**DEFAULT_LIMITS, **(limits or {})}
    budget = budget if budget is not None else WorkBudget(int(lim["path_work_limit"]))
    blockers = list(blockers)
    by_id = {s.symptom_id: s for s in symptoms}
    lanes_known = sorted({ln for s in symptoms for ln in s.lanes})
    lane_blockers = _blockers_by_lane(blockers, lanes_known)
    chain_level = sorted(b.blocker_id for b in blockers if b.code in CHAIN_LEVEL_CODES)
    links_incomplete = consistency.links_status != "complete"
    comps = _components(symptoms, links, links_incomplete=links_incomplete, blockers_by_lane=lane_blockers)
    comp_of = {m: c.component_id for c in comps for m in c.symptom_ids}

    dags = build_dags(links)
    en = enumerate_chains(dags, budget, int(lim["chains_reported"]), int(lim["count_work_limit"]))
    kept, total, truncated_by, state = en.kept, en.total, en.truncated_by, en.state

    new_blockers: list[Blocker] = []
    suppressed = (total - len(kept)) if total is not None else None
    dep = [ArtifactDigest(artifact_ref="dependencies", sha256=graph.sha256)] if graph is not None else []
    if truncated_by == "report_limit":
        new_blockers.append(make_blocker("chains_truncated", SourceRecord(kind="m3d_limit", ref="chains", field="limits.chains_reported"), count=suppressed, artifacts=dep))
    elif truncated_by == "work_limit":
        new_blockers.append(make_blocker("path_work_limit_reached", SourceRecord(kind="m3d_limit", ref="chains", field="limits.path_work_limit"), count=suppressed, artifacts=dep))
    tb_ids = [b.blocker_id for b in new_blockers]

    # components whose chain set is not completely reported are affected; the others keep the status they had (CHN-29)
    affected: set[str] = set()
    if truncated_by is not None:
        for d in dags:
            for h in d.sources:
                if (d.lane, h) not in state.done_heads:
                    affected.add(comp_of[h])
    comps = [c.model_copy(update={"defence": "not_defended", "blocker_ids": sorted(set(c.blocker_ids) | set(tb_ids))}) if c.component_id in affected else c
             for c in comps]
    cdef = {c.component_id: c for c in comps}

    # facts shared by every chain with the same head / lane
    caveat_codes = sorted({c.code for c in caveats if c.code in ("receipt_order_not_occurrence_order", "capture_loss_indistinguishable_from_silence",
                                                                  "baseline_from_recording", "baseline_contaminated_possible", "boundary_gaps_not_evaluated")})
    rec_ids = {lane: sorted(b.blocker_id for b in blockers if b.code == "recording_incomplete" and b.blocker_id in set(lane_blockers.get(lane, ()))) for lane in lanes_known}
    cov = coverage or {}
    by_node_lane: dict[tuple[str, str], list[Symptom]] = {}
    for s in symptoms:
        for lane in s.lanes:
            by_node_lane.setdefault((s.node, lane), []).append(s)
    for (n, lane), lst in by_node_lane.items():
        lst.sort(key=lambda s: (s.onset_in(lane).lower_ns, s.onset_in(lane).upper_ns, s.symptom_id))
    anc_cache: dict[tuple[str, str], CommonAncestorFacts] = {}

    def ancestors_of(lane: str, head: Symptom, chain_nodes: set[str]) -> CommonAncestorFacts:
        k = (lane, head.symptom_id)
        if k in anc_cache:
            return anc_cache[k]
        names = [a for a in (graph.ancestors(head.node) if graph is not None else []) if a not in chain_nodes]
        hi = head.onset_in(lane)
        items = []
        for a in names:
            lst = by_node_lane.get((a, lane), [])
            before = [s.symptom_id for s in lst[:bisect_left([s.onset_in(lane).lower_ns for s in lst], hi.lower_ns)] if s.onset_in(lane).precedes(hi)]
            c, dets = cov.get(a, ("not_covered", []))
            items.append(Ancestor(node=a, symptom_count_before_head=len(before), symptom_ids_before_head=sorted(before)[:SYMPTOMS_LISTED_PER_ANCESTOR], coverage=c,
                                  covering_detectors=dets))
        facts = CommonAncestorFacts(ancestors_total=len(items), ancestors=items[:ANCESTORS_LISTED])
        anc_cache[k] = facts
        return facts

    chains: list[Chain] = []
    for lane, path in kept:
        head = by_id[path[0]]
        nodes = [by_id[p].node for p in path]
        facts = ancestors_of(lane, head, set(nodes))
        dag = next(d for d in dags if d.lane == lane)
        comp = cdef[comp_of[path[0]]]
        bad = comp.defence == "not_defended"
        bids = sorted(set(comp.blocker_ids) | (set(chain_level) if bad else set()))     # lane blockers are already on the component
        any_width = any(by_id[p].onset_in(lane).width_ns > 0 for p in path)
        recs = rec_ids.get(lane, [])
        h2 = Hypothesis(template=T_COMMON, status="not_excluded", statement=HYPOTHESIS_SENTENCES[(T_COMMON, "not_excluded")], common_ancestors=facts,
                        discriminating_evidence_needed=_evidence("an intervention or a repeat recording", "a declared edge for the ancestor",
                                                                 *(["detector coverage of the ancestor's topics"] if any(a.coverage == "not_covered" for a in facts.ancestors) else [])))
        h3 = Hypothesis(template=T_ARTIFACT, status="not_excluded", statement=HYPOTHESIS_SENTENCES[(T_ARTIFACT, "not_excluded")],
                        artifact_facts=ArtifactFacts(caveat_codes=caveat_codes, recording_blocker_ids=recs),
                        discriminating_evidence_needed=_evidence("an intervention or a repeat recording", "recorder-side drop counters (not available in this format)",
                                                                 *(["a recording with the missing segments"] if recs else []),
                                                                 *(["a narrower onset interval from a higher-rate topic"] if any_width else [])))
        chains.append(Chain(
            chain_id="ch-" + hashlib.sha256(canonical_json({"lane": lane, "path": list(path)}).encode("utf-8")).hexdigest()[:16], lane=lane, head=path[0],
            symptom_ids=list(path), nodes=nodes, steps=[ChainStep(from_symptom=a, to_symptom=b, link_ids=sorted(dag.arc_links[(a, b)])) for a, b in zip(path, path[1:])],
            component_id=comp.component_id, defence="not_defended" if bad else "defended_structure", blocker_ids=bids, hypotheses=sorted([h2, h3], key=lambda h: h.template)))

    in_graph = set(graph.endpoints) if graph is not None else set()
    loc = Localization(components=_localize(comps, by_id) if graph is not None else [],
                       unlocalized=[Unlocalized(symptom_id=s.symptom_id, topic=s.topic, node=s.node) for s in sorted(symptoms, key=lambda s: (s.onset.domain, s.onset.lower_ns, s.onset.upper_ns, s.symptom_id))
                                    if graph is not None and s.node not in in_graph],
                       topic_layers=topic_layers(symptoms))
    status = "exact" if total is not None else "bounded_unknown"
    res = ChainAnalysis(components=comps, chains=chains, chains_reported=len(chains), chains_total=total, chains_total_status=status, chains_lower_bound=len(chains),
                        chains_truncated_by=truncated_by, link_hypotheses=sorted((link_hypothesis(l) for l in links), key=lambda h: h.link_id), localization=loc)
    return res, new_blockers
