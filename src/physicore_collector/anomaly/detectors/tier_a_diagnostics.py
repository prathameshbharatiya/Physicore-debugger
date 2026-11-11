"""tierA.diagnostics (DESIGN 3.2): DiagnosticStatus elements at WARN / ERROR / STALE, from DiagnosticArray topics and bare DiagnosticStatus topics."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..access import DecodeFailure, MessageView, _is_msg, elements, field, strict_int
from ..findings import ObservationRef, PayloadStampRef
from ..identity import KNOWN, classify_hardware_id, classify_text, render_raw, truncate_text
from ..runner import TopicCoverage
from ..severity import severity_for
from .base import Group, TierABase, note, occ_of

ARRAY = "diagnostic_msgs/msg/DiagnosticArray"
STATUS = "diagnostic_msgs/msg/DiagnosticStatus"
LEVEL_NAMES = ("OK", "WARN", "ERROR", "STALE")
FINDING_LEVELS = ("WARN", "ERROR", "STALE")


@dataclass
class Run:
    group: Group | None
    level: int
    absent: int = 0


class DiagnosticsDetector(TierABase):
    detector_id = "tierA.diagnostics"
    SUPPORTED = {ARRAY: {}, STATUS: {}}

    def consts_type(self, name: str) -> str:
        return STATUS

    def _field_type_problem(self, codec, name):
        ft = (codec.types or {}).get(STATUS, {}).get("level")
        if ft is not None and (ft.base not in ("uint8", "byte") or ft.array):
            return f"'level' is declared as {ft.base}{'[]' if ft.array else ''}, expected uint8/byte"
        return None

    def bind(self, topic_schemas, ev, config):
        ap = super().bind(topic_schemas, ev, config)
        self.open: dict[tuple, Run] = {}
        self.k = self.params.get("absent_elements_break_run_after")
        self.run_seq = 0
        return ap

    # ---------------------------------------------------------------------------------------------------------
    def _kv(self, el, mx: int, cap: int, meta: dict) -> list[list[str]]:
        """Inert key/value strings (never parsed), capped in count and in size; the exact count and any truncation are kept."""
        f, vs = field(el, "values")
        items = elements(vs) if f else None
        out: list[list[str]] = []
        meta["values_count_last"] = len(items or [])
        for kv in (items or [])[:cap]:
            kf, k = field(kv, "key")
            vf, v = field(kv, "value")
            pair = []
            for x in (k, v):
                y, o = truncate_text(x, mx) if isinstance(x, str) else ("", None)
                if o:
                    meta["trunc"] = max(meta.get("trunc", 0), o)
                pair.append(y)
            out.append(pair)
        return out

    def on_message(self, view: MessageView) -> None:
        L, st = self.ledger, self.state[view.topic]
        d = view.decoded()
        if isinstance(d, DecodeFailure):
            L.undecodable += 1
            return
        root = view.root()
        if view.payload_kind == "json" and not isinstance(root, dict):
            L.malformed += 1
            return
        bare = st.msg_type == STATUS
        if bare:
            els = [root]
        else:
            f, status = field(root, "status")
            els = elements(status) if f else None
            if els is None:
                L.malformed += 1                                 # `status` absent / not an array: not evaluated
                return
        by_value = {v: k for k, v in st.effective.items() if k in LEVEL_NAMES}
        occ = occ_of(view)
        mx, cap = self.lim.max_text_bytes, self.lim.max_retained_observations
        seen: set[tuple] = set()
        any_bad = False
        for i, el in enumerate(els):
            if not (isinstance(el, dict) or _is_msg(el)):
                L.malformed += 1
                any_bad = True
                continue
            lf, lv = field(el, "level")
            lv = strict_int(lv) if lf else None
            if lv is None:
                L.malformed += 1
                any_bad = True
                continue
            nf, name = field(el, "name")
            hf, hw = field(el, "hardware_id")
            ns, hs = classify_text(nf, name), classify_hardware_id(hf, hw)
            if ns in ("absent", "malformed"):
                L.ident("name")
            if hs in ("absent", "malformed"):
                L.ident("hardware_id")
            known = ns == KNOWN and hs == KNOWN
            key = (view.topic, name, hw) if known else None
            if known:
                seen.add(key)
            lname = by_value.get(lv)
            if lname is None:
                L.unrec(lv)
            if lname not in FINDING_LEVELS:
                if known:
                    self.open.pop(key, None)                    # the element appeared at another level: its run ends
                continue
            fp = "level" if bare else f"status[{i}].level"
            mf, message = field(el, "message")
            msg = message if mf and isinstance(message, str) else None
            ident_state, ident_raw = (ns, render_raw(ns, name, mx)) if ns != KNOWN else (hs, render_raw(hs, hw, mx))
            if not known and ns == KNOWN:
                ident_raw = "hardware_id=" + render_raw(hs, hw, mx)
            if not known:
                gkey = (view.topic, "unknown", occ.artifact_ref, occ.index, i)
                g = self.collector.start(gkey, occ, self._meta(lv, lname, name, hw, i, ns, hs, msg))
                run = None
            else:
                run = self.open.get(key)
                if run is not None and run.level == lv:
                    run.absent = 0
                    g = run.group
                else:
                    self.run_seq += 1
                    g = self.collector.start((view.topic, "known", name, hw, lv, self.run_seq), occ, self._meta(lv, lname, name, hw, i, ns, hs, msg))
                    self.open[key] = Run(g, lv)
            if g is None:
                continue

            def obs(fp=fp, ident_state=ident_state, ident_raw=ident_raw) -> ObservationRef:
                stamp = None
                sv, label = view.stamp("header.stamp") if not bare else (None, "")
                if sv is not None:
                    stamp = PayloadStampRef(value_ns=sv, clock_domain=f"payload_stamp:{view.topic}:header.stamp", stamp_type_check=label)
                return ObservationRef(artifact_ref=occ.artifact_ref, sha256=occ.sha256, topic=occ.topic, message_index=occ.index, order=occ.order,
                                      log_time_ns=occ.log_time_ns, field_path=fp, value=lv, payload_stamp=stamp,
                                      identity_state=("known" if known else ident_state), identity_raw=(None if known else ident_raw))
            self.collector.add(g, occ, obs)
            m = g.meta
            m["message_last"] = msg
            m["values_last"] = self._kv(el, mx, cap, m)
            if g.count == 1:
                m["values_first"] = m["values_last"]
                m["values_count_first"] = m["values_count_last"]
            if msg is not None:
                h = hashlib.sha256(msg.encode("utf-8", "surrogatepass")).hexdigest()
                if h not in m["distinct_hashes"]:
                    m["distinct_hashes"].add(h)
                    if len(m["distinct"]) < cap:
                        m["distinct"].append(msg)
            if self.uses_builtin(st, view, (lname,)):
                g.notes["constants_from_builtin_table"] = None
        if self.k is not None and not any_bad:
            for key in [k for k in self.open if k[0] == view.topic and k not in seen]:
                run = self.open[key]
                run.absent += 1
                if run.absent >= self.k:
                    del self.open[key]

    def _meta(self, lv, lname, name, hw, i, ns, hs, msg):
        mx, cap = self.lim.max_text_bytes, self.lim.max_retained_observations
        return {"level": lv, "level_name": lname, "name": name if ns == KNOWN else None, "hardware_id": hw if hs == KNOWN else None,
                "ns": ns, "hs": hs, "element_index": i, "message_first": msg, "message_last": msg, "distinct": [], "distinct_hashes": set(),
                "values_first": None, "values_last": None, "raw_name": name, "raw_hw": hw, "trunc": 0}

    def finish(self, cov: TopicCoverage):
        findings = []
        mx = self.lim.max_text_bytes
        for g in self.collector.groups:
            m = g.meta
            st = self.state[g.first.topic]
            known = g.key[1] == "known"
            lens = []

            def t(x):
                if not isinstance(x, str):
                    return x
                y, o = truncate_text(x, mx)
                if o:
                    lens.append(o)
                return y
            observed = {"level": m["level"], "level_name": m["level_name"], "name": t(m["name"]), "hardware_id": t(m["hardware_id"]),
                        "element_index": m["element_index"], "message_first": t(m["message_first"]), "message_last": t(m["message_last"]),
                        "distinct_messages": [t(x) for x in m["distinct"]], "distinct_messages_count": len(m["distinct_hashes"]),
                        "values_first": m["values_first"], "values_last": m["values_last"],
                        "values_count_first": m.get("values_count_first"), "values_count_last": m.get("values_count_last")}
            if m["trunc"]:
                lens.append(m["trunc"])
            notes = [note(c, p) for c, p in sorted(g.notes.items())]
            if not known:
                notes.append(note("identity_unknown", {"identity_state": m["ns"] if m["ns"] != KNOWN else m["hs"]}))
            if lens:
                notes.append(note("text_truncated", {"original_length": max(lens)}))
            fpath = g.obs[0].field_path if g.obs else None
            template = "level" if st.msg_type == STATUS else "status[*].level"
            gk = ["known", g.key[2], g.key[3], m["level"]] if known else ["unknown", g.key[2], g.key[3], g.key[4]]
            sev = severity_for(f"diagnostic.{m['level_name']}")
            expl = (f"Level {m['level_name']} ({m['level']}) was declared by the diagnostic source in {g.count} occurrence(s) of one element "
                    f"from log_time {g.first.log_time_ns} to {g.last.log_time_ns} ns; first at message index {g.first.index}.")
            findings.append(self.base_finding(g, cov, message_type=st.msg_type, severity=sev, category="diagnostic_status", field_path=fpath,
                                              template=template, group_key=gk, observed=observed, explanation=expl, notes=notes))
        return self.assemble(cov, findings)
