"""The `LocalizationReport` (schema 1.0), its canonical JSON and the readable renderer (Step 8; DESIGN 3.3, FR-RPT-*, FR-PRE-03).

The readable report and the JSON come from the same model. Every sentence of the readable report is built from a fixed template; text that comes from
the data (topic names, logger names, identities) is inert: control characters are removed, it is truncated to 80 characters, quoted with double quotes
(a double quote inside data becomes a single quote) and never placed inside a sentence of its own."""
from __future__ import annotations

import re
from typing import Optional

from pydantic import StrictInt, StrictStr, field_validator, model_validator

from . import LOCALIZATION_SCHEMA_VERSION
from .blockers import Blocker, Caveat
from .chains import Chain, Localization, LinkHypothesis
from .consistency import Component, EdgeCoverage, Link
from .precedence import Lane
from .report import Strict, find_floats, find_forbidden_keys, report_json
from .symptoms import Symptom
from .vocabulary import CHAINS_TOTAL_STATUS, SEVERITY_DISCLAIMER

EXIT_REASONS = ("anomaly_config_invalid", "expectations_invalid", "dependencies_invalid", "evidence_not_inspectable", "evidence_partial_or_unverified",
                "evidence_changed_during_run", "completed")
PROBLEM_SOURCES = ("anomaly_config", "expectations", "dependencies", "normalisation", "signal_profile", "evidence")
DATA_CHARS = 80

LIMITATIONS = (
    "Receipt order is not occurrence order; transport, QoS and recorder queueing are invisible.",
    "A chain is a structure in the observed symptoms plus the user's declarations; the declarations cannot be verified by this tool.",
    "The detectors determine what can be a symptom; a fault that no enabled detector reports does not appear here.",
    "Gap and rate symptoms cannot tell capture loss from silence; mode baselines come from the recording itself.",
    "Validation so far is on synthetic recordings; validation on real robot recordings and the decoder licensing review are outstanding.",
    "Mixed or unrelated clocks give several earliest symptoms; this tool does not relate them.",
    "The memory and performance statements of the anomaly detection step are unchanged; its measured overhead exceeds its stated budget.",
)


class DetectorStatus(Strict):
    detector_id: StrictStr
    status: StrictStr


class Problem(Strict):
    source: StrictStr
    path: StrictStr
    message: StrictStr

    @field_validator("source")
    @classmethod
    def _s(cls, v: str) -> str:
        if v not in PROBLEM_SOURCES:
            raise ValueError(f"source must be one of {PROBLEM_SOURCES}")
        return v


class EvidenceDigest(Strict):
    artifact_ref: StrictStr
    sha256: StrictStr


class DependencyInput(Strict):
    sha256: StrictStr
    semantic_hash: StrictStr
    edge_count: StrictInt


class Inputs(Strict):
    anomaly_config_hash: Optional[StrictStr] = None
    expectations_sha256: Optional[StrictStr] = None
    dependencies: Optional[DependencyInput] = None


class Coverage(Strict):
    dependencies: StrictStr                           # declared | none_declared
    detectors: list[DetectorStatus]
    m2_status: Optional[StrictStr] = None
    edge_coverage: list[EdgeCoverage] = []
    links_total: StrictInt = 0
    links_suppressed: StrictInt = 0
    links_status: Optional[StrictStr] = None
    path_pairs_related: StrictInt = 0
    path_pairs_examined: StrictInt = 0
    path_pairs_not_examined: StrictInt = 0
    no_declared_relation_count: StrictInt = 0
    cross_lane_pairs_not_compared: StrictInt = 0
    cross_lane_statement: Optional[StrictStr] = None
    chains_reported: StrictInt = 0
    chains_lower_bound: StrictInt = 0
    chains_truncated_by: Optional[StrictStr] = None
    symptoms_suppressed: StrictInt = 0

    @field_validator("dependencies")
    @classmethod
    def _d(cls, v: str) -> str:
        if v not in ("declared", "none_declared"):
            raise ValueError("dependencies is declared or none_declared")
        return v


class LocalizationReport(Strict):
    localization_schema_version: StrictStr = LOCALIZATION_SCHEMA_VERSION
    tool_version: StrictStr
    evidence: list[EvidenceDigest]
    inputs: Inputs
    parameters_hash: StrictStr
    limits: dict[StrictStr, StrictInt]
    lanes: list[Lane]
    symptoms: list[Symptom]
    links: list[Link]
    components: list[Component]
    chains: list[Chain]
    chains_total: Optional[StrictInt] = None
    chains_total_status: StrictStr
    hypotheses: list[LinkHypothesis]
    blockers: list[Blocker]
    caveats: list[Caveat]
    localization: Localization
    coverage: Coverage
    limitations: list[StrictStr]
    severity_disclaimer: StrictStr
    exit_code: StrictInt
    exit_reasons: list[StrictStr]
    problems: Optional[list[Problem]] = None

    @model_validator(mode="after")
    def _ok(self) -> "LocalizationReport":
        if self.chains_total_status not in CHAINS_TOTAL_STATUS:
            raise ValueError(f"chains_total_status must be one of {CHAINS_TOTAL_STATUS}")
        if (self.chains_total_status == "exact") != (self.chains_total is not None):
            raise ValueError("chains_total is present exactly when it is exact")
        if self.exit_code not in (0, 1, 2, 3) or not self.exit_reasons or any(r not in EXIT_REASONS for r in self.exit_reasons):
            raise ValueError("exit code in 0..3 with reasons from the closed list")
        if self.severity_disclaimer != SEVERITY_DISCLAIMER or list(self.limitations) != list(LIMITATIONS):
            raise ValueError("the severity disclaimer and the limitations are part of every report")
        if self.exit_code == 3 and not self.problems:
            raise ValueError("an invalid input (exit 3) is reported with its problems")
        return self


def to_json(report: LocalizationReport) -> str:
    """Canonical JSON: sorted keys, integers only, absent optionals omitted, trailing newline. Raises on a float or a forbidden key."""
    bad = find_forbidden_keys(report.model_dump(mode="json"))
    if bad:
        raise ValueError(f"forbidden key in report: {bad[0]}")
    return report_json(report)


# ------------------------------------------------------------------------------------------------------------------ readable renderer

_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


def inert(text: object) -> str:
    """Data text made safe: control characters removed, truncated to 80 characters, double-quoted (inner double quotes become single quotes)."""
    s = _CTRL.sub(" ", str(text)).replace('"', "'")
    if len(s) > DATA_CHARS:
        s = s[: DATA_CHARS - 1] + "…"
    return f'"{s}"'


def _ival(iv) -> str:
    return f"[{iv.lower_ns}, {iv.upper_ns}] ns" if iv.lower_ns != iv.upper_ns else f"{iv.lower_ns} ns"


# fixed sentence templates: {name} marks the only places data may appear
T = {
    "title": "M3d localization report (schema {schema}, tool {tool})",
    "scope": "Scope: {sentence}",
    "severity": "Severity: {sentence}",
    "evidence": "Evidence: {n} recording artifact(s); inputs: anomaly configuration {cfg}, expectations {exp}, dependencies {dep}.",
    "exit": "Exit code {code}: {reasons}.",
    "counts": "Counts: {symptoms} symptom(s), {links} link(s) reported of {links_total}, {components} component(s), {chains} chain(s) reported, {blockers} blocker(s), {caveats} caveat(s).",
    "no_deps": "No dependencies declared: this report gives symptoms, lanes, layers and earliest symptoms only; it has no links, chains or hypotheses.",
    "problem": "Input problem ({source}) at {path}: {message}",
    "lane": "Lane {domain}: {n} symptom(s) in {layers} layer(s).",
    "earliest_sentence": "  Statement for this lane: {sentence}",
    "earliest_ids": "  Earliest symptom(s) in this lane: {ids}.",
    "earliest_blockers": "  Blockers recorded for this lane: {ids}.",
    "cross_lane": "Across lanes: {sentence}",
    "symptom": "Symptom {id}: source {source} {rule}, category {category}, topic {topic}, node {node}, onset {onset} (basis {basis}, lanes {lanes}), severity {severity} as reported by the source.",
    "symptom_identity": "  Identity as reported by the source: {kind} {value}.",
    "symptom_flags": "  Source coverage {coverage}; truncated {truncated}; notes {notes}.",
    "more": "... {n} more {what} not listed here; the JSON report lists all of them.",
    "link": "Link {id}: lane {lane}, symptom {a} on {na} then symptom {b} on {nb} along edge(s) {edges}: label {label}; separation {smin} to {smax} ns; window {window}.",
    "link_via": "  Through node {node}: symptoms strictly between {between}; detector coverage {coverage} ({dets}).",
    "link_note": "  Notes: {notes}.",
    "link_hyp": "  Declared-edge reading: {status}. {sentence}",
    "component": "Component {id}: symptoms {ids}; links {n_links}; defence {defence}{blockers}.",
    "chain": "Chain {id} (lane {lane}, head {head}): {ids}; nodes {nodes}; defence {defence}{blockers}.",
    "chain_hyp": "  Hypothesis {template}: {status}. {sentence}",
    "chain_hyp_anc": "    Declared ancestors of the head node not on the chain: {total} ({listed} listed): {names}.",
    "chain_hyp_need": "    Evidence that would discriminate: {needs}.",
    "chains_total_exact": "Chains: {n} in total (exact).",
    "chains_total_unknown": "Chains: total not computed within the count limit (bounded_unknown); at least {n} exist.",
    "chains_trunc": "Chains listed: {n}; the list is incomplete ({why}).",
    "loc_head": "Localization (where symptoms were observed; not where a fault is):",
    "loc_component": "  Component {id}, lane {lane}: first layer on nodes {locus}; last layer on nodes {frontier}.",
    "loc_unlocalized": "  Not localized (node has no declared edge): symptom {id} on {node}.",
    "loc_topic": "  Lane {lane}, topic {topic}, layer {layer}: symptoms {ids}.",
    "blocker": "Blocker {id} [{code}]: {detail} Scope: detector {detector}, topic {topic}, lane {lane}. Count {count}. Source: {kind} {ref} field {field}.",
    "blocker_art": "  Artifacts in scope: {arts}. Observation references: {obs}.",
    "caveat": "Caveat [{code}]: {detail} Scope: detector {detector}.",
    "limitation": "Limitation {n}: {text}",
    "edge_cov": "Edge {id} ({a} to {b}): {sa} symptom(s) on the first node, {sb} on the second; {status}.",
    "undeclared": "Pairs of symptoms with no declared path in either direction (counted per lane): {n}; pairs with a declared path but no common lane: {m}.",
}
_LIST_LIMIT = 200


def template_regex(name: str) -> "re.Pattern[str]":
    parts = re.split(r"\{[a-z_]+\}", T[name])
    return re.compile("^" + "(?:.*)".join(re.escape(p) for p in parts) + "$", re.DOTALL)


def _join(items, default="none") -> str:
    items = list(items)
    return ", ".join(str(i) for i in items) if items else default


def render_text(r: LocalizationReport) -> str:
    out: list[tuple[str, str]] = []

    def emit(name: str, **kw: object) -> None:
        text = T[name].format(**kw)
        out.append((name, text))

    emit("title", schema=r.localization_schema_version, tool=r.tool_version)
    from .vocabulary import SCOPE_SENTENCE
    emit("scope", sentence=SCOPE_SENTENCE)
    emit("severity", sentence=r.severity_disclaimer)
    emit("evidence", n=len(r.evidence), cfg=r.inputs.anomaly_config_hash or "none", exp=r.inputs.expectations_sha256 or "none",
         dep=(r.inputs.dependencies.sha256 if r.inputs.dependencies else "none declared"))
    emit("exit", code=r.exit_code, reasons=", ".join(r.exit_reasons))
    for p in r.problems or []:
        emit("problem", source=p.source, path=inert(p.path), message=inert(p.message))
    emit("counts", symptoms=len(r.symptoms), links=len(r.links), links_total=r.coverage.links_total, components=len(r.components), chains=len(r.chains),
         blockers=len(r.blockers), caveats=len(r.caveats))
    if r.coverage.dependencies == "none_declared":
        emit("no_deps")
    for ln in r.lanes:
        emit("lane", domain=ln.domain, n=ln.symptom_count, layers=len(ln.layers))
        emit("earliest_sentence", sentence=ln.earliest.statement)
        if ln.earliest.symptom_ids:
            emit("earliest_ids", ids=_join(ln.earliest.symptom_ids))
        if ln.earliest.blocker_ids:
            emit("earliest_blockers", ids=_join(ln.earliest.blocker_ids))
    if r.coverage.cross_lane_statement:
        emit("cross_lane", sentence=r.coverage.cross_lane_statement)
    for s in r.symptoms[:_LIST_LIMIT]:
        emit("symptom", id=s.symptom_id, source=s.source, rule=s.detector_or_rule, category=s.category, topic=inert(s.topic), node=inert(s.node),
             onset=_ival(s.onset), basis=s.onset_basis, lanes=_join(sorted({s.onset.domain, *[p.lane for p in s.secondary_onsets]})), severity=s.severity)
        for k, v in sorted(s.identities.items()):
            emit("symptom_identity", kind=k, value=inert(v))
        emit("symptom_flags", coverage=s.source_coverage.topic_coverage, truncated="yes" if s.truncated else "no", notes=_join(sorted(s.notes)))
    if len(r.symptoms) > _LIST_LIMIT:
        emit("more", n=len(r.symptoms) - _LIST_LIMIT, what="symptoms")
    hyp = {h.link_id: h for h in r.hypotheses}
    from .chains import HYPOTHESIS_SENTENCES
    for l in r.links[:_LIST_LIMIT]:
        emit("link", id=l.link_id, lane=l.lane, a=l.from_symptom, na=inert(l.from_node), b=l.to_symptom, nb=inert(l.to_node), edges=_join(l.edge_ids),
             label=l.label, smin=l.sep_min_ns, smax=l.sep_max_ns, window=("none declared" if l.window_ns is None else f"{l.window_ns} ns"))
        for n in l.intermediates:
            emit("link_via", node=inert(n.node), between=_join(n.symptom_ids_between), coverage=n.coverage, dets=_join(n.covering_detectors))
        emit("link_note", notes=_join(l.notes))
        h = hyp.get(l.link_id)
        if h:
            emit("link_hyp", status=h.status, sentence=HYPOTHESIS_SENTENCES[(h.template, h.status)])
    if len(r.links) > _LIST_LIMIT:
        emit("more", n=len(r.links) - _LIST_LIMIT, what="links")
    for c in r.components[:_LIST_LIMIT]:
        emit("component", id=c.component_id, ids=_join(c.symptom_ids), n_links=len(c.link_ids), defence=c.defence,
             blockers=(f" (blockers {_join(c.blocker_ids)})" if c.blocker_ids else ""))
    if len(r.components) > _LIST_LIMIT:
        emit("more", n=len(r.components) - _LIST_LIMIT, what="components")
    for ch in r.chains[:_LIST_LIMIT]:
        emit("chain", id=ch.chain_id, lane=ch.lane, head=ch.head, ids=" then ".join(ch.symptom_ids), nodes=" then ".join(inert(n) for n in ch.nodes), defence=ch.defence,
             blockers=(f" (blockers {_join(ch.blocker_ids)})" if ch.blocker_ids else ""))
        for h in ch.hypotheses:
            emit("chain_hyp", template=h.template, status=h.status, sentence=h.statement)
            if h.common_ancestors:
                emit("chain_hyp_anc", total=h.common_ancestors.ancestors_total, listed=len(h.common_ancestors.ancestors),
                     names=_join((inert(a.node) for a in h.common_ancestors.ancestors)))
            emit("chain_hyp_need", needs="; ".join(h.discriminating_evidence_needed))
    if len(r.chains) > _LIST_LIMIT:
        emit("more", n=len(r.chains) - _LIST_LIMIT, what="chains")
    if r.coverage.dependencies == "declared":
        if r.chains_total is not None:
            emit("chains_total_exact", n=r.chains_total)
        else:
            emit("chains_total_unknown", n=r.coverage.chains_lower_bound)
        if r.coverage.chains_truncated_by:
            emit("chains_trunc", n=r.coverage.chains_reported, why=r.coverage.chains_truncated_by)
        emit("loc_head")
        lanes_of = {c.component_id: c for c in r.localization.components}
        for c in r.localization.components[:_LIST_LIMIT]:
            for ln in c.lanes:
                emit("loc_component", id=c.component_id, lane=ln.lane, locus=_join(inert(n) for n in ln.locus_nodes), frontier=_join(inert(n) for n in ln.frontier_nodes))
        for u in r.localization.unlocalized[:_LIST_LIMIT]:
            emit("loc_unlocalized", id=u.symptom_id, node=inert(u.node))
        for e in r.coverage.edge_coverage[:_LIST_LIMIT]:
            emit("edge_cov", id=inert(e.edge_id), a=inert(e.from_node), b=inert(e.to_node), sa=e.from_symptom_count, sb=e.to_symptom_count, status=e.status)
        emit("undeclared", n=r.coverage.no_declared_relation_count, m=r.coverage.cross_lane_pairs_not_compared)
    else:
        emit("loc_head")
    for t in r.localization.topic_layers[:_LIST_LIMIT]:
        emit("loc_topic", lane=t.lane, topic=inert(t.topic), layer=t.layer, ids=_join(t.symptom_ids))
    for b in r.blockers:
        emit("blocker", id=b.blocker_id, code=b.code, detail=b.detail, detector=b.scope.detector or "any", topic=inert(b.scope.topic) if b.scope.topic else "any",
             lane=b.scope.lane or "every lane", count=("unknown" if b.count is None else b.count), kind=b.source.kind, ref=inert(b.source.ref), field=b.source.field)
        emit("blocker_art", arts=_join(f"{a.artifact_ref} {a.sha256 or 'no digest'}" for a in b.artifacts),
             obs=_join(f"{o.artifact_ref} message {o.message_index}" for o in b.observations))
    for c in r.caveats:
        emit("caveat", code=c.code, detail=c.detail, detector=c.scope.detector or "any")
    for i, text in enumerate(r.limitations, 1):
        emit("limitation", n=i, text=text)
    return "\n".join(t for _, t in out) + "\n"


def render_lines(r: LocalizationReport) -> list[tuple[str, str]]:
    """(template name, text) for every generated line; used to prove that every sentence comes from a fixed template."""
    text = render_text(r)
    names = list(T)
    res = []
    for line in text.rstrip("\n").split("\n"):
        hit = next((n for n in names if template_regex(n).match(line)), None)
        res.append((hit or "UNMATCHED", line))
    return res
