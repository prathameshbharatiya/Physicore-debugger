"""Shared machinery of the Tier A detectors: ledger, grouping collector with exact counts and a findings cap, topic binding, constants
resolution, result-status decision and finding assembly. Detectors import only access, findings, clock, severity, identity and this module."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..access import DecodeFailure, MessageView, normalise_type, schema_constants
from ..config import AnomalyConfig
from ..findings import (ClockedTime, CoverageStatement, DetectorResult, Finding, FindingsTruncated, ObservationRef, Reason, SourceRef,
                        UncertaintyNote, derive_finding_id, hash_parameters)
from ..clock import LOG_TIME_DOMAIN
from ..ros_constants import builtin
from ..runner import APPLICABLE, Applicability, TopicCoverage


@dataclass
class Ledger:
    undecodable: int = 0
    malformed: int = 0                                   # payload_malformed_field (evaluation fields)
    unrecognised: dict[str, int] = field(default_factory=dict)
    identity_malformed: dict[str, int] = field(default_factory=dict)

    def unrec(self, v: Any) -> None:
        k = str(v)
        self.unrecognised[k] = self.unrecognised.get(k, 0) + 1

    def ident(self, name: str) -> None:
        self.identity_malformed[name] = self.identity_malformed.get(name, 0) + 1


@dataclass
class Occ:
    artifact_ref: str
    sha256: str
    topic: str
    index: int
    order: int
    log_time_ns: int


def occ_of(view: MessageView) -> Occ:
    return Occ(view.artifact_ref, view.sha256, view.topic, view.message_index, view.order, view.log_time_ns)


@dataclass
class Group:
    key: tuple
    first: Occ
    last: Occ
    count: int = 0
    obs: list[ObservationRef] = field(default_factory=list)
    artifacts: set[tuple[str, str]] = field(default_factory=set)
    notes: dict[str, dict | None] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    closed: bool = False


class Collector:
    """Groups occurrences by key. Exact counts, ALL contributing artifacts tracked, at most `max_obs` retained references per group,
    at most `max_findings` groups kept; the exact number of suppressed findings is reported (never reduced silently)."""

    def __init__(self, max_findings: int, max_obs: int):
        self.max_findings, self.max_obs = max_findings, max_obs
        self.groups: list[Group] = []
        self._by_key: dict[tuple, Group] = {}
        self._suppressed: set[tuple] = set()

    def get(self, key: tuple) -> Group | None:
        return self._by_key.get(key)

    def start(self, key: tuple, occ: Occ, meta: dict[str, Any] | None = None) -> Group | None:
        """New group (closing nothing). Returns None when the findings cap suppresses it."""
        if len(self.groups) >= self.max_findings:
            self._suppressed.add(key)
            return None
        g = Group(key, occ, occ, meta=dict(meta or {}))
        self.groups.append(g)
        self._by_key[key] = g
        return g

    def add(self, g: Group, occ: Occ, obs: Callable[[], ObservationRef]) -> None:
        g.count += 1
        g.last = occ
        g.artifacts.add((occ.artifact_ref, occ.sha256))
        if len(g.obs) < self.max_obs:
            g.obs.append(obs())

    def admit(self, key: tuple) -> bool:
        """Count one finding against the cap; False (and counted as suppressed, exactly) when the cap is reached."""
        if len(self.groups) >= self.max_findings:
            self._suppressed.add(key)
            return False
        self.groups.append(Group(key, None, None))   # type: ignore[arg-type]  # placeholder: Tier B builds its groups itself
        return True

    def suppress_repeat(self, key: tuple) -> bool:
        """An occurrence of a group that the cap suppressed: keeps the suppressed-finding count exact."""
        if key in self._suppressed:
            return True
        return False

    @property
    def suppressed(self) -> int:
        return len(self._suppressed)


def note(code: str, params: dict | None = None) -> UncertaintyNote:
    return UncertaintyNote(code=code, params=params)


def coverage_statement(cov: TopicCoverage, undecodable: int) -> CoverageStatement:
    return CoverageStatement(topic_coverage="complete" if cov.complete else "incomplete", incomplete_reasons=list(cov.incomplete_reasons),
                             messages_examined=cov.messages_examined, messages_undecodable=undecodable,
                             recording_span_ns=cov.coverage)


@dataclass
class TopicState:
    msg_type: str
    embedded: dict[str, int]          # constants of this type from the RECORDED schema (authoritative)
    effective: dict[str, int]         # embedded first, built-in only where the schema carries none
    builtin_names: set[str]           # names whose value came ONLY from the built-in table


class DetectorCommon:
    """Finding assembly shared by every detector (needs self.params, pparams_hash, lim, ledger, collector, state, detector_id/version)."""
    detector_id = ""
    detector_version = "1.0.0"

    def decide(self, cov: TopicCoverage, n_findings: int) -> tuple[str, list[Reason]]:
        raise NotImplementedError

    def assemble(self, cov: TopicCoverage, findings: list[Finding]) -> tuple[DetectorResult, list[Finding]]:
        status, reasons = self.decide(cov, len(findings))
        L = self.ledger
        res = DetectorResult(
            detector_id=self.detector_id, detector_version=self.detector_version, status=status, topics=sorted(self.state),
            messages_examined=cov.messages_examined, messages_undecodable=L.undecodable, findings_count=len(findings), reasons=reasons,
            unrecognised_values=dict(sorted(L.unrecognised.items())) or None, payload_malformed_field=L.malformed,
            identity_field_malformed=dict(sorted(L.identity_malformed.items())) or None,
            findings_truncated=(FindingsTruncated(detector=self.detector_id, suppressed_exact_count=self.collector.suppressed)
                                if self.collector.suppressed else None),
            parameters_hash=self.pparams_hash)
        return res, findings

    def base_finding(self, g: Group, cov: TopicCoverage, *, message_type: str, severity: tuple[str, str], category: str,
                     field_path: str | None, template: str | None, group_key: Any, observed: Any, explanation: str,
                     notes: list[UncertaintyNote], times: tuple[int, int | None] | None = None, obs_truncated: bool | None = None,
                     artifacts: list[tuple[str, str]] | None = None, expected: Any = None, expected_source: str | None = None,
                     threshold: Any = None, clock_domain: str = LOG_TIME_DOMAIN, basis: str = "mcap_log_time") -> Finding:
        topic = g.first.topic
        arts = sorted(set(artifacts)) if artifacts is not None else sorted(g.artifacts)
        notes = list(notes)
        truncated = (g.count > len(g.obs)) if obs_truncated is None else obs_truncated
        if truncated:
            notes.append(note("retained_observations_truncated", {"retained": len(g.obs), "occurrences": g.count}))
        fid = derive_finding_id(detector_id=self.detector_id, detector_version=self.detector_version, parameters_hash=self.pparams_hash,
                                contributing_artifacts=arts, topic=topic, field_path_template=template, group_key=group_key,
                                first_artifact_ref=g.first.artifact_ref, first_message_index=g.first.index)
        first_ns, last_ns = times if times is not None else (g.first.log_time_ns, g.last.log_time_ns if g.count > 1 else None)
        return Finding(
            finding_id=fid, detector_id=self.detector_id, detector_version=self.detector_version, severity=severity[0],
            severity_basis=severity[1], anomaly_category=category, topic=topic, message_type=message_type, field_path=field_path,
            field_path_template=template, first_observed_at=ClockedTime(value_ns=first_ns, clock_domain=clock_domain),
            last_observed_at=(ClockedTime(value_ns=last_ns, clock_domain=clock_domain) if last_ns is not None else None),
            timestamp_basis=basis, clock_domain=clock_domain, observed_value=observed, expected_value=expected,
            expected_source=expected_source, threshold=threshold, occurrence_count=g.count,
            observations=list(g.obs), observations_truncated=truncated, source=[SourceRef(artifact_ref=r, sha256=s_) for r, s_ in arts],
            coverage=coverage_statement(cov, self.ledger.undecodable), uncertainty=sorted(notes, key=lambda n: n.code),
            detector_parameters=self.params, explanation=explanation)


class TierABase(DetectorCommon):
    """Template for a Tier A detector. Subclasses set SUPPORTED (normalised type -> evaluation-field expectations)."""
    needs_decode = True
    SUPPORTED: dict[str, dict[str, Any]] = {}
    NEEDED_NAMES: dict[str, tuple[str, ...]] = {}     # type -> constant names this detector depends on
    JSON_ONLY: frozenset[str] = frozenset()

    # -- selection -----------------------------------------------------------------------------------------------
    def _topic_param(self, config: AnomalyConfig):
        return config.parameters(self.detector_id).get("topics", "auto")

    def candidates(self, topic_schemas: dict[str, set[str | None]], config: AnomalyConfig) -> set[str]:
        p = self._topic_param(config)
        if p == "auto":
            return {t for t, names in topic_schemas.items() if any(normalise_type(n) in self.SUPPORTED for n in names)}
        return {t for t in p if t in topic_schemas}

    def bind(self, topic_schemas, ev, config) -> Applicability:
        self.config = config
        self.params = config.parameters(self.detector_id)
        self.pparams_hash = hash_parameters(self.params)
        self.lim = config.limits
        self.cov_reasons: list[Reason] = []
        self.ledger = Ledger()
        self.state: dict[str, TopicState] = {}
        self.collector = Collector(self.lim.max_findings_per_detector, self.lim.max_retained_observations)
        p = self._topic_param(config)
        listed = None if p == "auto" else list(p)
        usable: list[str] = []
        blind: list[Reason] = []
        notapp: list[Reason] = []
        if listed is not None:
            for t in listed:
                if t not in topic_schemas:
                    notapp.append(Reason(code="topic_not_recorded", detail=t))
        for t in sorted(self.candidates(topic_schemas, config)):
            names = {normalise_type(n) for n in topic_schemas[t]}
            if len(names) > 1:
                blind.append(Reason(code="mixed_types_on_topic", detail=t))
                continue
            (name,) = names
            if name not in self.SUPPORTED:
                notapp.append(Reason(code="type_not_supported", detail=f"{t}: {name}"))
                continue
            st, why = self._topic_state(t, name, ev)
            if st is None:
                blind.append(Reason(code=why[0], detail=why[1]))
            else:
                self.state[t] = st
                usable.append(t)
        if usable:
            self.cov_reasons = blind
            return Applicability(APPLICABLE, usable, [])
        if blind:
            return Applicability("blind_spot", [], blind)
        return Applicability("not_applicable", [], notapp or [Reason(code="no_topic_of_supported_type")])

    def _topic_state(self, topic: str, name: str, ev) -> tuple[TopicState | None, tuple[str, str]]:
        enc = {e for e in ev.topic_encodings.get(topic, set())}
        if not enc <= {"json", "cdr"}:
            return None, ("message_encoding_unsupported", f"{topic}: {sorted(enc)}")
        codecs = ev.topic_codecs.get(topic, [])
        if name in self.JSON_ONLY and codecs:
            return None, ("message_encoding_unsupported", f"{topic}: {name} is supported for JSON payloads only")
        embedded: dict[str, int] = {}
        for c in codecs:
            if c.status != "ok":
                continue
            emb = schema_constants(c.schema.data, c.root_type).get(self.consts_type(name), {})
            for k, v in emb.items():
                if embedded.setdefault(k, v) != v:
                    return None, ("constants_mismatch", f"{topic}: channels disagree on {k}")
            bad = self._field_type_problem(c, name)
            if bad:
                return None, ("field_type_mismatch", f"{topic}: {bad}")
        table = builtin(self.consts_type(name))
        for k, v in embedded.items():
            if k in table and table[k] != v:
                return None, ("constants_mismatch", f"{topic}: {k} is {v} in the recorded schema, {table[k]} in the built-in table")
        effective = dict(table)
        effective.update(embedded)
        only = {k for k in effective if k not in embedded}
        return TopicState(name, embedded, effective, only), ("", "")

    def _field_type_problem(self, codec, name: str) -> str | None:
        return None

    def consts_type(self, name: str) -> str:
        """The message type whose constants matter for a topic of type `name` (a container's element type, for instance)."""
        return name

    def uses_builtin(self, st: TopicState, view: MessageView, names: tuple[str, ...]) -> bool:
        return view.payload_kind == "json" or any(n in st.builtin_names for n in names)

    # -- results -------------------------------------------------------------------------------------------------
    def extra_status(self, cov: TopicCoverage, n_findings: int) -> tuple[list[Reason], bool]:
        """Detector-specific reasons, and whether they degrade `no_anomaly_observed` (default: none)."""
        return [], False

    def decide(self, cov: TopicCoverage, n_findings: int) -> tuple[str, list[Reason]]:
        L = self.ledger
        reasons: list[Reason] = list(self.cov_reasons)
        if not cov.complete:
            reasons.append(Reason(code="recording_incomplete", detail=(cov.incomplete_reasons[0] if cov.incomplete_reasons else None)))
        if L.undecodable:
            reasons.append(Reason(code="undecodable_messages", count=L.undecodable))
        if L.malformed:
            reasons.append(Reason(code="unparseable_messages", count=L.malformed))
        if L.unrecognised:
            reasons.append(Reason(code="unrecognised_values", count=sum(L.unrecognised.values())))
        if n_findings:
            return "findings_reported", reasons
        extra_reasons, extra_degrade = self.extra_status(cov, n_findings)
        reasons += extra_reasons
        degraded = (not cov.complete) or L.undecodable or L.malformed or L.unrecognised or extra_degrade
        if degraded:
            return "insufficient_evidence", reasons
        if cov.messages_examined == 0:
            return "insufficient_evidence", reasons + [Reason(code="no_messages_on_in_scope_topic")]
        return "no_anomaly_observed", reasons
