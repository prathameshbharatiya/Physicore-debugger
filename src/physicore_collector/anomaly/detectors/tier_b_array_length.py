"""tierB.array_length (DESIGN 6.5): a typed sequence field (`T[]` / `T[<=N]`) whose length differs from an expected length that comes from exactly one of
two sources: a config rule (`expected_length`) or the recording's own mode (`baseline: mode`, with `min_observations` / `min_share`). No default length
is ever invented. Lengths are run-length encoded in receipt order (state O(#changes)); one finding per run whose length differs from the expected one.
Descriptive only: a changed length is reported as observed, never as a fault."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field as dfield
from fractions import Fraction
import re

from ..access import DecodeFailure, MessageView, elements, field as get_field, plain
from ..config import AnomalyConfig
from ..findings import ObservationRef, Reason, hash_parameters
from ..runner import APPLICABLE, Applicability, TopicCoverage
from ..schemaidx import parse as parse_schema, resolve
from ..severity import severity_for
from .base import Collector, DetectorCommon, Group, Ledger, Occ, note, occ_of

_STEP = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\*|\d+)\])?$")


@dataclass
class Run:
    length: int
    first: Occ
    last: Occ
    count: int
    first_seq: int
    last_seq: int
    obs: list[ObservationRef]
    arts: set[tuple[str, str]]


@dataclass
class Series:
    runs: list[Run] = dfield(default_factory=list)


@dataclass
class RuleState:
    topic: str
    path: str
    expected: int | None
    baseline: bool
    min_obs: int = 0
    min_share: Fraction = Fraction(0)
    series: dict[str, Series] = dfield(default_factory=dict)
    lengths: Counter = dfield(default_factory=Counter)
    observations: int = 0
    wrong_type: int = 0
    absent: int = 0
    wrong_first: str = ""


def walk(root, path: str):
    """Expand `a[*].b` / `a[2].b` over a decoded value. Yields (concrete path, status, node): status "ok" | "absent" (field missing or JSON null)."""
    out: list[tuple[str, str, object]] = []

    def rec(node, steps, prefix):
        if not steps:
            out.append((prefix, "ok", node))
            return
        m = _STEP.match(steps[0])
        if m is None or isinstance(node, DecodeFailure):
            out.append((prefix, "absent", None))
            return
        name, idx = m.group(1), m.group(2)
        ok, child = get_field(node, name)
        here = f"{prefix}.{name}" if prefix else name
        if not ok or child is None:
            out.append((here if idx is None else here + "[*]", "absent", None))
            return
        if idx is None:
            rec(child, steps[1:], here)
            return
        els = elements(child)
        if els is None:
            out.append((here, "absent", None))
            return
        if idx == "*":
            for i, e in enumerate(els):
                rec(e, steps[1:], f"{here}[{i}]")
        else:
            i = int(idx)
            if i < len(els):
                rec(els[i], steps[1:], f"{here}[{i}]")
            else:
                out.append((f"{here}[{i}]", "absent", None))

    rec(root, path.split("."), "")
    return out


class ArrayLengthDetector(DetectorCommon):
    detector_id = "tierB.array_length"
    detector_version = "1.0.0"
    needs_decode = True

    def candidates(self, topic_schemas, config: AnomalyConfig) -> set[str]:
        return {r["topic"] for r in config.parameters(self.detector_id)["rules"] if r["topic"] in topic_schemas}

    def bind(self, topic_schemas, ev, config) -> Applicability:
        self.params = config.parameters(self.detector_id)
        self.pparams_hash = hash_parameters(self.params)
        self.lim = config.limits
        self.ledger = Ledger()
        self.collector = Collector(self.lim.max_findings_per_detector, self.lim.max_retained_observations)
        self.types = {t: (sorted(n for n in names if n) or [""])[0] for t, names in topic_schemas.items()}
        self.rules: list[RuleState] = []
        self.state: dict[str, list] = {}                 # topics in scope (the result's `topics`)
        self.by_topic: dict[str, list[RuleState]] = {}
        self.reasons: list[Reason] = []
        self.arts: dict[str, dict[int, tuple[str, str]]] = {}
        self.seq: dict[str, int] = {}
        self.undecodable_kinds: Counter = Counter()
        blind: list[Reason] = []
        notapp: list[Reason] = []
        for r in self.params["rules"]:
            t, path = r["topic"], r["path"]
            tag = f"{t} {path}"
            if t not in topic_schemas:
                notapp.append(Reason(code="topic_not_recorded", detail=t))
                continue
            if len(topic_schemas[t]) > 1:
                blind.append(Reason(code="mixed_types_on_topic", detail=t))
                continue
            verdict = self._schema_verdict(ev, t, path)
            if verdict is not None:
                (notapp if verdict.code == "fixed_size_array" else blind).append(Reason(code=verdict.code, detail=tag))
                continue
            rs = RuleState(t, path, r.get("expected_length"), "baseline" in r)
            if rs.baseline:
                rs.min_obs, rs.min_share = r["min_observations"], Fraction(r["min_share"])
            self.rules.append(rs)
            self.by_topic.setdefault(t, []).append(rs)
        self.state = dict(self.by_topic)
        self.rule_reasons = blind + notapp
        if self.rules:
            return Applicability(APPLICABLE, sorted(self.by_topic), [])
        if blind:
            return Applicability("blind_spot", [], blind + notapp)
        return Applicability("not_applicable", [], notapp or [Reason(code="no_rule_for_recorded_topic")])

    def _schema_verdict(self, ev, topic: str, path: str):
        """None when the rule is evaluable; else a Reason-like with the code (structural fixed size / blind-spot cause)."""
        codecs = ev.topic_codecs.get(topic, [])
        if not codecs:
            return None                                   # JSON: no schema; each message is observed
        okc = [c for c in codecs if c.status == "ok"]
        if not okc:
            return Reason(code="payload_not_decoded")
        verdicts: list[str] = []
        for c in okc:
            types = parse_schema(c.root_type, c.schema.data)
            kind, ft = resolve(types, c.root_type, path)
            if kind == "ok" and ft is not None:
                verdicts.append({"fixed": "fixed_size_array", "scalar": "path_not_an_array", "sequence": ""}[ft.kind])
            else:
                verdicts.append({"field_missing": "field_not_in_schema", "not_resolvable": "schema_unresolved", "bad_path": "path_invalid"}[kind])
        for v in verdicts:
            if v:
                return Reason(code=v)
        return None

    def on_message(self, view: MessageView) -> None:
        d = view.decoded()
        if isinstance(d, DecodeFailure):
            self.ledger.undecodable += 1
            self.undecodable_kinds[d.kind] += 1
            return
        topic = view.topic
        seq = self.seq[topic] = self.seq.get(topic, 0) + 1
        self.arts.setdefault(topic, {}).setdefault(view.order, (view.artifact_ref, view.sha256))
        occ = occ_of(view)
        root = view.root()
        cap = self.lim.max_retained_observations
        for rs in self.by_topic.get(topic, []):
            for cpath, status, node in walk(root, rs.path):
                if status == "absent":
                    rs.absent += 1
                    continue
                els = elements(node)
                if els is None:
                    rs.wrong_type += 1
                    self.ledger.malformed += 1
                    rs.wrong_first = rs.wrong_first or f"{occ.artifact_ref}#{occ.index} {cpath}"
                    continue
                n = len(els)
                rs.lengths[n] += 1
                rs.observations += 1
                s = rs.series.setdefault(cpath, Series())
                ref = ObservationRef(artifact_ref=occ.artifact_ref, sha256=occ.sha256, topic=topic, message_index=occ.index, order=occ.order,
                                     log_time_ns=occ.log_time_ns, field_path=cpath, value=n, value_repr=str(n))
                run = s.runs[-1] if s.runs else None
                if run is not None and run.length == n and run.last_seq == seq - 1:
                    run.count += 1
                    run.last, run.last_seq = occ, seq
                    run.arts.add((occ.artifact_ref, occ.sha256))
                    if len(run.obs) < cap:
                        run.obs.append(ref)
                else:
                    s.runs.append(Run(n, occ, occ, 1, seq, seq, [ref][:cap], {(occ.artifact_ref, occ.sha256)}))

    # ---------------------------------------------------------------------------------------------------
    def finish(self, cov: TopicCoverage):
        findings = []
        self.rule_notes: list[Reason] = []
        self.rule_status: list[str] = []         # "evaluated" | "insufficient" | "blind" per rule
        for rs in self.rules:
            tag = f"{rs.topic} {rs.path}"
            if rs.wrong_type and not rs.observations:
                self.rule_notes.append(Reason(code="path_not_an_array", detail=f"{tag}: first at {rs.wrong_first}", count=rs.wrong_type))
                self.rule_status.append("blind")
                continue
            if rs.wrong_type:
                self.rule_notes.append(Reason(code="path_not_an_array", detail=f"{tag}: first at {rs.wrong_first}", count=rs.wrong_type))
            if not rs.observations:
                self.rule_notes.append(Reason(code="no_observations", detail=tag))
                self.rule_status.append("insufficient")
                continue
            if rs.baseline:
                top = rs.lengths.most_common()
                best = top[0][1]
                tied = [ln for ln, c in top if c == best]
                if len(tied) > 1:
                    self.rule_notes.append(Reason(code="baseline_tie", detail=f"{tag}: lengths {sorted(tied)} each seen {best} times", count=best))
                    self.rule_status.append("insufficient")
                    continue
                if rs.observations < rs.min_obs:
                    self.rule_notes.append(Reason(code="too_few_observations", detail=f"{tag}: {rs.observations} < {rs.min_obs}", count=rs.observations))
                    self.rule_status.append("insufficient")
                    continue
                if Fraction(best, rs.observations) < rs.min_share:
                    self.rule_notes.append(Reason(code="baseline_share_below_min", detail=f"{tag}: {best}/{rs.observations} < {rs.min_share}",
                                                  count=best))
                    self.rule_status.append("insufficient")
                    continue
                expected, source = tied[0], "recording_baseline"
            else:
                expected, source = rs.expected, "config"
            self.rule_status.append("evaluated")
            all_arts = sorted(set(self.arts.get(rs.topic, {}).values()))
            for cpath in sorted(rs.series):
                for run in rs.series[cpath].runs:
                    if run.length == expected:
                        continue
                    key = ("array_length", rs.topic, rs.path, cpath, run.first.artifact_ref, run.first.index)
                    if not self.collector.admit(key):
                        continue
                    g = Group(key, run.first, run.last, run.count, list(run.obs), set(run.arts))
                    arts = all_arts if source == "recording_baseline" else sorted(run.arts)
                    notes = [note("baseline_from_recording")] if source == "recording_baseline" else []
                    observed = {"length": run.length, "expected_length": expected, "messages_in_run": run.count, "path": cpath,
                                "baseline_share": (f"{rs.lengths[expected]}/{rs.observations}" if source == "recording_baseline" else None)}
                    expl = (f"The sequence at {cpath} had {run.length} element(s) in {run.count} consecutive message(s); the expected length is "
                            f"{expected} (source: {'configured rule' if source == 'config' else 'most common length in this recording'}). "
                            "A length that differs from the expected one is reported as observed; it is not by itself evidence of a fault, "
                            "and a legitimately variable array will be reported too.")
                    findings.append(self.base_finding(
                        g, cov, message_type=self.types.get(rs.topic, ""), severity=severity_for("tierB.array_length"), category="array_length_change",
                        field_path=cpath, template=rs.path, group_key=["array_length", cpath, run.length, expected], observed=observed,
                        explanation=expl, notes=notes, times=(run.first.log_time_ns, run.last.log_time_ns if run.count > 1 else None),
                        obs_truncated=run.count > len(g.obs), artifacts=arts, expected=expected, expected_source=source))
        return self.assemble(cov, findings)

    def decide(self, cov: TopicCoverage, n_findings: int):
        L = self.ledger
        reasons = list(self.rule_reasons) + list(self.rule_notes)
        if not cov.complete:
            reasons.append(Reason(code="recording_incomplete", detail=(cov.incomplete_reasons[0] if cov.incomplete_reasons else None)))
        if L.undecodable:
            reasons.append(Reason(code="undecodable_messages", count=L.undecodable))
            for k, n in sorted(self.undecodable_kinds.items()):
                reasons.append(Reason(code=f"undecodable:{k}", count=n))
        if n_findings:
            return "findings_reported", reasons
        if all(s == "blind" for s in self.rule_status) and self.rule_status:
            return "blind_spot", reasons
        if any(s != "evaluated" for s in self.rule_status) or not cov.complete or L.undecodable or L.malformed:
            return "insufficient_evidence", reasons
        if cov.messages_examined == 0:
            return "insufficient_evidence", reasons + [Reason(code="no_messages_on_in_scope_topic")]
        return "no_anomaly_observed", reasons
