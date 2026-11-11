"""tierB.non_finite (DESIGN 6.6): NaN / +Inf / -Inf in the float leaves of explicitly opted-in topics. The walker visits EVERY leaf of every in-scope
message (no early exit), so `occurrence_count` (offending leaves) and `messages_with_offenders` are exact; only the retained observation list is
capped. One finding per run of consecutive messages that contain an offender for the group key (topic, field-path template, value class).
Nothing is said about cause: NaN/Inf may be an intentional 'unmeasured' marker (use `exclude_paths`)."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from ..access import DecodeFailure, MessageView, iter_leaves
from ..bitinfo import bit_info, self_check
from ..config import AnomalyConfig
from ..findings import ObservationRef, Reason, hash_parameters
from ..runner import APPLICABLE, Applicability, TopicCoverage
from ..schemaidx import parse as parse_schema, resolve, template as to_template
from ..severity import severity_for
from .base import Collector, DetectorCommon, Group, Ledger, note, occ_of

_NUMERIC_STRINGS = frozenset({"nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"})


def compile_glob(pattern: str) -> re.Pattern:
    """`ranges`, `poses[*].position`, `values[3]`: `[*]` matches any index, everything else is literal; a pattern also covers everything below it."""
    parts = re.split(r"(\[\*\])", pattern)
    rx = "".join(r"\[\d+\]" if p == "[*]" else re.escape(p) for p in parts)
    return re.compile(rf"^{rx}(?:[.\[].*)?$")


def classify(v) -> str | None:
    """'nan' | 'inf' | '-inf' for a non-finite real, else None (ints, bools, strings, null and finite floats are never offenders)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return None if v.is_finite() else ("nan" if v.is_nan() else ("inf" if v > 0 else "-inf"))
    if isinstance(v, float):
        if v != v:
            return "nan"
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
    return None


@dataclass
class OpenRun:
    group: Group | None
    last_seq: int


class NonFiniteDetector(DetectorCommon):
    detector_id = "tierB.non_finite"
    detector_version = "1.0.0"
    needs_decode = True

    def candidates(self, topic_schemas, config: AnomalyConfig) -> set[str]:
        return {t for t in config.parameters(self.detector_id)["topics"] if t in topic_schemas}

    def bind(self, topic_schemas, ev, config) -> Applicability:
        self.ev = ev
        self.params = config.parameters(self.detector_id)
        self.pparams_hash = hash_parameters(self.params)
        self.lim = config.limits
        self.ledger = Ledger()
        self.collector = Collector(self.lim.max_findings_per_detector, self.lim.max_retained_observations)
        self.types = {t: (sorted(n for n in names if n) or [""])[0] for t, names in topic_schemas.items()}
        self.inc = [compile_glob(p) for p in self.params["include_paths"]]
        self.exc = [compile_glob(p) for p in self.params["exclude_paths"]]
        self.f64_ok = self_check()
        self.seq: dict[str, int] = {}
        self.open: dict[tuple, OpenRun] = {}
        self.run_no = 0
        self.arts_seen: dict[tuple[str, str], int] = {}
        self.width_cache: dict[tuple[str, str], int | None] = {}
        self.json_null = 0
        self.numeric_strings = 0
        self.lenient = 0
        self.fail_kinds: Counter = Counter()
        self.decodable = 0
        self.topic_reasons: list[Reason] = []
        usable, notrec, blind = [], [], []
        for t in self.params["topics"]:
            if t not in topic_schemas:
                notrec.append(Reason(code="topic_not_recorded", detail=t))
            elif not ev.topic_encodings.get(t, set()) <= {"json", "cdr"}:
                blind.append(Reason(code="message_encoding_unsupported", detail=f"{t}: {sorted(ev.topic_encodings.get(t, set()))}"))
            else:
                usable.append(t)
        self.state = {t: None for t in usable}          # topics in scope (the result's `topics`)
        self.topic_reasons = blind + notrec
        if usable:
            return Applicability(APPLICABLE, usable, [])
        if blind:
            return Applicability("blind_spot", [], blind + notrec)
        return Applicability("not_applicable", [], notrec)

    # ------------------------------------------------------------------------------------------------
    def _selected(self, path: str) -> bool:
        if self.inc and not any(r.match(path) for r in self.inc):
            return False
        return not any(r.match(path) for r in self.exc)

    def _width(self, topic: str, tmpl: str) -> int | None:
        key = (topic, tmpl)
        if key not in self.width_cache:
            widths = set()
            for c in self.ev.topic_codecs.get(topic, []):
                if c.status != "ok":
                    continue
                kind, ft = resolve(parse_schema(c.root_type, c.schema.data), c.root_type, tmpl)
                widths.add({"float32": 32, "float64": 64}.get(ft.base) if kind == "ok" and ft is not None else None)
            self.width_cache[key] = widths.pop() if len(widths) == 1 else None
        return self.width_cache[key]

    def on_message(self, view: MessageView) -> None:
        topic = view.topic
        d = view.decoded()
        root = d
        if isinstance(d, DecodeFailure):
            if view.payload_kind == "json" and d.kind == "json_unparseable":
                root = view.lenient_json()
                if not isinstance(root, DecodeFailure):
                    self.lenient += 1
            if isinstance(root, DecodeFailure):
                self.ledger.undecodable += 1
                self.fail_kinds[d.kind] += 1
                return
        else:
            root = view.root()
        self.decodable += 1
        seq = self.seq[topic] = self.seq.get(topic, 0) + 1
        occ = occ_of(view)
        cap = self.lim.max_retained_observations
        found: dict[tuple[str, str], list] = {}
        is_json = view.payload_kind == "json"
        for leaf in iter_leaves(root):
            path = leaf.path
            if (self.inc or self.exc) and not self._selected(path):
                continue
            if leaf.kind == "float":
                cls = classify(leaf.value)
                if cls is None:
                    continue
                ent = found.setdefault((to_template(path), cls), [0, []])
                ent[0] += 1
                if len(ent[1]) < cap:
                    ent[1].append((path, leaf.value, cls))
            elif is_json and leaf.kind == "none":
                self.json_null += 1
            elif is_json and leaf.kind == "str" and leaf.value.strip().lower() in _NUMERIC_STRINGS:
                self.numeric_strings += 1
        for (tmpl, cls), (n, items) in found.items():
            base = (topic, tmpl, cls)
            cur = self.open.get(base)
            g: Group | None
            if cur is not None and cur.last_seq == seq - 1:
                g = cur.group
                cur.last_seq = seq
                if g is not None:
                    g.count += n
                    g.meta["msgs"] += 1
                    g.last = occ
            else:
                self.run_no += 1
                g = self.collector.start((topic, tmpl, cls, self.run_no), occ, {"msgs": 1, "tmpl": tmpl, "cls": cls})
                if g is not None:
                    g.count = n
                self.open[base] = OpenRun(g, seq)
            if g is None:
                continue
            g.artifacts.add((occ.artifact_ref, occ.sha256))
            width = self._width(topic, tmpl) if not is_json else None
            for path, val, c in items:
                if len(g.obs) >= cap:
                    break
                g.obs.append(ObservationRef(
                    artifact_ref=occ.artifact_ref, sha256=occ.sha256, topic=topic, message_index=occ.index, order=occ.order, log_time_ns=occ.log_time_ns,
                    field_path=path, value=c, value_repr=c, bit_info=bit_info(float(val), width, self.f64_ok) if not is_json else None))

    # ------------------------------------------------------------------------------------------------
    def finish(self, cov: TopicCoverage):
        findings = []
        for g in self.collector.groups:
            m = g.meta
            tmpl, cls, topic = m["tmpl"], m["cls"], g.first.topic
            notes = []
            width = None if self.ev.topic_codecs.get(topic) is None else self._width(topic, tmpl)
            if cls == "nan" and width == 32:
                notes.append(note("nan_payload_bits_not_guaranteed"))
            observed = {"value_class": cls, "offending_leaves": g.count, "messages_with_offenders": m["msgs"], "field_path_template": tmpl}
            expl = (f"A non-finite value ({cls}) was observed at {tmpl} in {m['msgs']} consecutive message(s), {g.count} offending leaf value(s) in total. "
                    "Only the observation is reported; NaN or Inf may be an intentional 'no measurement' marker in some message types.")
            findings.append(self.base_finding(
                g, cov, message_type=self.types.get(topic, ""), severity=severity_for("tierB.non_finite"), category="non_finite_value",
                field_path=(tmpl if "[*]" not in tmpl else None), template=tmpl, group_key=["non_finite", tmpl, cls], observed=observed,
                explanation=expl, notes=notes))
        return self.assemble(cov, findings)

    def decide(self, cov: TopicCoverage, n_findings: int):
        L = self.ledger
        reasons = list(self.topic_reasons)
        if not cov.complete:
            reasons.append(Reason(code="recording_incomplete", detail=(cov.incomplete_reasons[0] if cov.incomplete_reasons else None)))
        if L.undecodable:
            reasons.append(Reason(code="undecodable_messages", count=L.undecodable))
            if self.fail_kinds.get("payload_too_large"):
                reasons.append(Reason(code="payload_too_large", count=self.fail_kinds["payload_too_large"]))
        if self.lenient:
            reasons.append(Reason(code="json_nonfinite_tokens_parsed", count=self.lenient))
        if self.json_null:
            reasons.append(Reason(code="json_null_not_inspected", count=self.json_null))
        if self.numeric_strings:
            reasons.append(Reason(code="numeric_string_not_interpreted", count=self.numeric_strings))
        if n_findings:
            return "findings_reported", reasons
        if not cov.complete or L.undecodable:
            return "insufficient_evidence", reasons
        if self.decodable == 0:
            return "insufficient_evidence", reasons + [Reason(code="no_messages_on_in_scope_topic")]
        return "no_anomaly_observed", reasons
