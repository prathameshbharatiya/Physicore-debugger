"""tierB.stamp_order (DESIGN 6.4): backward and duplicate header timestamps of configured fields, in the payload clock domain
`payload_stamp:<topic>:<field>`, compared only within (topic, field[, partition]). Stamp validity and nanoseconds come from the accepted M3b
function through MessageView.stamp (no second implementation here); invalid stamps are never evaluated and never hidden."""
from __future__ import annotations

from dataclasses import dataclass, field as dfield

from ..access import DecodeFailure, MessageView, path_value, plain, raw_stamp_text, stamp_label
from ..findings import ObservationRef, PayloadStampRef, Reason
from ..severity import severity_for
from ..runner import TopicCoverage
from .base import Group, Occ, note, occ_of
from .timing import TimingBase
from ..clock import payload_stamp_domain

NOT_EVALUATED = ("rejected_type", "field_not_in_schema", "schema_unresolved", "payload_not_decoded", "mixed_payloads")


@dataclass
class Part:
    prev_ns: int | None = None
    prev_occ: Occ | None = None
    prev_ref: ObservationRef | None = None
    valid: int = 0
    excluded_since_valid: int = 0
    run: Group | None = None          # open duplicate run (None also while a run is open but suppressed by the findings cap)
    in_run: bool = False
    run_value: int | None = None
    run_n: int = 0


@dataclass
class Chan:
    topic: str
    field: str
    label: str
    category: str
    parts: dict[tuple, Part] = dfield(default_factory=dict)
    invalid: int = 0
    invalid_first: list[str] = dfield(default_factory=list)
    excluded_total: int = 0


class StampOrderDetector(TimingBase):
    detector_id = "tierB.stamp_order"
    detector_version = "1.0.0"
    needs_decode = True

    def bind(self, topic_schemas, ev, config):
        ap = super().bind(topic_schemas, ev, config)
        self.chans: dict[tuple[str, str], Chan] = {}
        self.blind: list[Reason] = []
        self.partition_fields: list[str] = list(self.params.get("partition_by", []))
        for t in sorted(self.state):
            for f in self.params["fields"]:
                label = stamp_label(ev, t, f)
                cat = label.split(":", 1)[0]
                self.chans[(t, f)] = Chan(t, f, label, cat)
                if cat in NOT_EVALUATED:
                    self.blind.append(Reason(code=cat, detail=f"{t} {f}: {label}"))
        self.state = {t: None for t in self.state}                # type: ignore[assignment]  # only the topic list is needed
        self.run_seq = 0
        self.findings_groups: list[tuple[Group, dict]] = []
        self.valid_any = False
        return ap

    def on_message(self, view: MessageView) -> None:
        L = self.ledger
        d = view.decoded()
        if isinstance(d, DecodeFailure):
            L.undecodable += 1
            return
        occ = occ_of(view)
        mx = self.lim.max_text_bytes
        root = view.root()
        pkey: tuple | None = ()
        pvals = {}
        for pf in self.partition_fields:
            ok, v = path_value(root, pf)
            v = plain(v) if ok else None
            if not ok or isinstance(v, bool) or not isinstance(v, (str, int)):
                pkey = None
                break
            pvals[pf] = v
        if pkey is not None:
            pkey = tuple(sorted(pvals.items()))
        for f in self.params["fields"]:
            ch = self.chans[(view.topic, f)]
            if ch.category in NOT_EVALUATED:
                continue
            if pkey is None:
                L.malformed += 1                                   # partition value missing or ill-typed: not evaluated by this detector
                continue
            ns, label = view.stamp(f)
            if ns is None:
                ch.invalid += 1
                if len(ch.invalid_first) < self.lim.max_retained_observations:
                    raw = raw_stamp_text(root, f)
                    ch.invalid_first.append(f"{occ.artifact_ref}#{occ.index} (log_time {occ.log_time_ns} ns): {raw}"[:mx * 2])
                for part in ch.parts.values():
                    part.excluded_since_valid += 1
                ch.excluded_total += 1
                continue
            part = ch.parts.setdefault(pkey, Part())
            part.valid += 1
            ref = ObservationRef(artifact_ref=occ.artifact_ref, sha256=occ.sha256, topic=occ.topic, message_index=occ.index, order=occ.order,
                                 log_time_ns=occ.log_time_ns, field_path=f, value=None, value_repr=str(ns),
                                 payload_stamp=PayloadStampRef(value_ns=ns, clock_domain=payload_stamp_domain(view.topic, f), stamp_type_check=label))
            if part.prev_ns is not None:
                if ns < part.prev_ns:
                    self._close_run(part)
                    self._backward(ch, part, occ, ref, ns, pkey)
                elif ns == part.prev_ns:
                    if not part.in_run:
                        part.in_run = True
                        self.run_seq += 1
                        key = (view.topic, f, "dup", pkey, ns, self.run_seq)
                        g = self.collector.start(key, part.prev_occ, {"ch": ch, "pkey": pkey, "value": ns, "excluded": part.excluded_since_valid})
                        part.run, part.run_value, part.run_n = g, ns, 0
                        if g is not None:
                            g.count = 1
                            g.artifacts.add((part.prev_occ.artifact_ref, part.prev_occ.sha256))
                            g.obs.append(part.prev_ref)
                    if part.run is not None:
                        g = part.run
                        g.count += 1
                        g.last = occ
                        g.artifacts.add((occ.artifact_ref, occ.sha256))
                        if len(g.obs) < self.lim.max_retained_observations:
                            g.obs.append(ref)
                        if part.excluded_since_valid:
                            g.meta["excluded"] = g.meta.get("excluded", 0) + part.excluded_since_valid
                else:
                    self._close_run(part)
            part.prev_ns, part.prev_occ, part.prev_ref = ns, occ, ref
            part.excluded_since_valid = 0

    def _close_run(self, part: Part) -> None:
        part.run = None
        part.in_run = False
        part.run_value = None

    def _backward(self, ch: Chan, part: Part, occ: Occ, ref: ObservationRef, ns: int, pkey: tuple) -> None:
        key = (ch.topic, ch.field, "back", pkey, occ.artifact_ref, occ.index)
        g = self.collector.start(key, occ, {"ch": ch, "pkey": pkey, "prev": part.prev_ns, "cur": ns, "excluded": part.excluded_since_valid})
        if g is None:
            return
        g.count = 1
        g.artifacts.update({(part.prev_occ.artifact_ref, part.prev_occ.sha256), (occ.artifact_ref, occ.sha256)})
        g.obs.extend([part.prev_ref, ref][: self.lim.max_retained_observations])
        g.meta["prev_occ"] = part.prev_occ

    # ---------------------------------------------------------------------------------------------------
    def finish(self, cov: TopicCoverage):
        findings = []
        for g in self.collector.groups:
            m = g.meta
            ch: Chan = m["ch"]
            dom = payload_stamp_domain(ch.topic, ch.field)
            part_obj = {k: v for k, v in m["pkey"]}
            notes = []
            if ch.category == "structure_only":
                notes.append(note("structure_only_stamp_type"))
            if m.get("excluded"):
                notes.append(note("invalid_stamps_excluded", {"excluded_messages_between": m["excluded"]}))
            if "cur" in m:
                mag = m["prev"] - m["cur"]
                observed = {"field": ch.field, "previous_stamp_ns": m["prev"], "stamp_ns": m["cur"], "magnitude_ns": mag, "partition": part_obj or None}
                expl = (f"Stamp {m['cur']} ns of field {ch.field} is {mag} ns lower than the previous valid stamp {m['prev']} ns "
                        f"(message index {g.first.index} follows message index {m['prev_occ'].index} in receipt order).")
                cat, gk, first, last = "timestamp_regression", ["backward", m["prev"], m["cur"], list(map(list, m["pkey"]))], m["cur"], None
            else:
                v = m["value"]
                observed = {"field": ch.field, "stamp_ns": v, "messages_in_run": g.count, "repeats": g.count - 1, "partition": part_obj or None}
                expl = f"{g.count} consecutive messages carried the identical valid stamp {v} ns in field {ch.field}."
                if v == 0:
                    notes.append(note("zero_stamp_valid_not_assumed_unset"))
                    expl += " The value 0 is treated as an ordinary stamp value."
                cat, gk, first, last = "timestamp_duplicate", ["duplicate", v, list(map(list, m["pkey"]))], v, None
            f = self.base_finding(
                g, cov, message_type=self.types.get(ch.topic, ""), severity=severity_for("tierB.stamp_order"), category=cat, field_path=ch.field,
                template=ch.field, group_key=gk, observed=observed, explanation=expl, notes=notes, times=(first, last),
                obs_truncated=(g.count > len(g.obs)) if cat == "timestamp_duplicate" else False, clock_domain=dom, basis="payload_stamp")
            findings.append(f)
        return self.assemble(cov, findings)

    def decide(self, cov: TopicCoverage, n_findings: int):
        L = self.ledger
        reasons = list(self.topic_reasons) + list(self.blind)
        invalid = sum(c.invalid for c in self.chans.values())
        for c in sorted(self.chans.values(), key=lambda c: (c.topic, c.field)):
            if c.invalid:
                reasons.append(Reason(code="invalid_stamps", detail=f"{c.topic} {c.field}: first at " + " | ".join(c.invalid_first), count=c.invalid))
        if L.undecodable:
            reasons.append(Reason(code="undecodable_messages", count=L.undecodable))
        if L.malformed:
            reasons.append(Reason(code="unparseable_messages", count=L.malformed))
        if not cov.complete:
            reasons.append(Reason(code="recording_incomplete", detail=(cov.incomplete_reasons[0] if cov.incomplete_reasons else None)))
        enough = any(p.valid >= 2 for c in self.chans.values() for p in c.parts.values())
        if n_findings:
            return "findings_reported", reasons
        evaluable = [c for c in self.chans.values() if c.category not in NOT_EVALUATED]
        if invalid or (self.chans and not evaluable):
            return "blind_spot", reasons
        if not cov.complete or L.undecodable or L.malformed:
            return "insufficient_evidence", reasons
        if not enough:
            return "insufficient_evidence", reasons + [Reason(code="too_few_valid_stamps")]
        return "no_anomaly_observed", reasons
