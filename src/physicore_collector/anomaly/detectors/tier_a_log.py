"""tierA.ros_log (DESIGN 3.1): ROS log messages at WARN / ERROR / FATAL of the message's own type."""
from __future__ import annotations

import hashlib

from ..access import DecodeFailure, MessageView, field, strict_int
from ..findings import ObservationRef, PayloadStampRef, Reason
from ..identity import KNOWN, UNKNOWN_STATES, classify_text, render_raw, truncate_text
from ..runner import TopicCoverage
from ..severity import severity_for
from .base import Occ, TierABase, note, occ_of

LEVEL_NAMES = ("DEBUG", "INFO", "WARN", "ERROR", "FATAL")
FINDING_LEVELS = ("WARN", "ERROR", "FATAL")
STAMP_FIELD = {"rcl_interfaces/msg/Log": "stamp", "rosgraph_msgs/msg/Log": "header.stamp"}


class RosLogDetector(TierABase):
    detector_id = "tierA.ros_log"
    SUPPORTED = {"rcl_interfaces/msg/Log": {}, "rosgraph_msgs/msg/Log": {}}
    JSON_ONLY = frozenset({"rosgraph_msgs/msg/Log"})      # ROS 1 serialisation is not CDR (DESIGN 3.1)

    def _field_type_problem(self, codec, name):
        ft = (codec.types or {}).get(name, {}).get("level")
        if ft is not None and (ft.base not in ("uint8", "byte") or ft.array):
            return f"'level' is declared as {ft.base}{'[]' if ft.array else ''}, expected uint8/byte"
        return None

    def on_message(self, view: MessageView) -> None:
        L, st = self.ledger, self.state[view.topic]
        d = view.decoded()
        if isinstance(d, DecodeFailure):
            L.undecodable += 1
            return
        root = view.root()
        if not isinstance(root, dict) and view.payload_kind == "json":
            L.malformed += 1
            return
        found, lv = field(root, "level")
        lv = strict_int(lv) if found else None
        if lv is None:
            L.malformed += 1                                     # evaluation field: not evaluated, counted
            return
        by_value = {v: k for k, v in st.effective.items() if k in LEVEL_NAMES}
        lname = by_value.get(lv)
        nfound, name = field(root, "name")
        nstate = classify_text(nfound, name)
        if nstate in ("absent", "malformed"):
            L.ident("name")                                      # identity accounting only; never changes severity
        if lname is None:
            L.unrec(lv)
            return
        if lname not in FINDING_LEVELS:
            return
        mfound, msg = field(root, "msg")
        occ = occ_of(view)
        mx = self.lim.max_text_bytes
        text_key = ("s", msg) if mfound and isinstance(msg, str) else ("x", type(msg).__name__ if mfound else "absent")
        if nstate == KNOWN:
            key = (view.topic, "known", name, lv, text_key)
        else:
            key = (view.topic, "unknown", occ.artifact_ref, occ.index)
        g = self.collector.get(key)
        if g is None:
            g = self.collector.start(key, occ, {"level": lv, "level_name": lname, "name": name if nstate == KNOWN else None,
                                                "nstate": nstate, "msg": msg if mfound else None, "mfound": mfound})
            if g is None:
                return
        ident_raw = None if nstate == KNOWN else render_raw(nstate, name, mx)

        def obs() -> ObservationRef:
            stamp = None
            sv, label = view.stamp(STAMP_FIELD[st.msg_type])
            if sv is not None:
                stamp = PayloadStampRef(value_ns=sv, clock_domain=f"payload_stamp:{view.topic}:{STAMP_FIELD[st.msg_type]}", stamp_type_check=label)
            return ObservationRef(artifact_ref=occ.artifact_ref, sha256=occ.sha256, topic=occ.topic, message_index=occ.index, order=occ.order,
                                  log_time_ns=occ.log_time_ns, field_path="level", value=lv, payload_stamp=stamp,
                                  identity_state=nstate, identity_raw=ident_raw)
        self.collector.add(g, occ, obs)
        if self.uses_builtin(st, view, (lname,)):
            g.notes["constants_from_builtin_table"] = None

    def finish(self, cov: TopicCoverage):
        findings = []
        mx = self.lim.max_text_bytes
        for g in self.collector.groups:
            m = g.meta
            st = self.state[g.first.topic]
            text, orig = truncate_text(m["msg"], mx) if isinstance(m["msg"], str) else (m["msg"], None)
            nm, norig = truncate_text(m["name"], mx) if isinstance(m["name"], str) else (m["name"], None)
            observed = {"level": m["level"], "level_name": m["level_name"], "name": nm, "msg": text}
            notes = [note(c, p) for c, p in sorted(g.notes.items())]
            if m["nstate"] != KNOWN:
                notes.append(note("identity_unknown", {"identity_state": m["nstate"]}))
            big = max([x for x in (orig, norig) if x] or [0])
            if big:
                notes.append(note("text_truncated", {"original_length": big}))
            tkey = hashlib.sha256(str(g.key[-1]).encode("utf-8", "surrogatepass")).hexdigest()[:16] if m["nstate"] == KNOWN else None
            group_key = ["known", g.key[2], g.key[3], tkey] if m["nstate"] == KNOWN else ["unknown", g.key[2], g.key[3]]
            sev = severity_for(f"log.{m['level_name']}")
            expl = (f"Level {m['level_name']} ({m['level']}) was declared by the message source in {g.count} occurrence(s) "
                    f"from log_time {g.first.log_time_ns} to {g.last.log_time_ns} ns; first at message index {g.first.index}.")
            findings.append(self.base_finding(g, cov, message_type=st.msg_type, severity=sev, category="log_severity", field_path="level",
                                              template="level", group_key=group_key, observed=observed, explanation=expl, notes=notes))
        return self.assemble(cov, findings)
