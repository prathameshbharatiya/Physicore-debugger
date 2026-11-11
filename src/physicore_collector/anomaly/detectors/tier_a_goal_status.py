"""tierA.goal_status (DESIGN 3.3, D1): goals reported ABORTED (both families) or CANCELED (action_msgs only). Known goal identity groups by goal
and status; unknown identity (empty/absent/malformed id, all-zero/absent/wrong-length uuid) is one finding per occurrence."""
from __future__ import annotations

from ..access import DecodeFailure, MessageView, _is_msg, elements, field, number_text, plain, strict_int
from ..findings import ObservationRef, Reason
from ..identity import KNOWN, classify_text, classify_uuid, render_raw, truncate_text
from ..runner import TopicCoverage
from ..severity import severity_for
from .base import TierABase, note, occ_of

ACTIONLIB_ARR = "actionlib_msgs/msg/GoalStatusArray"
ACTIONLIB_ST = "actionlib_msgs/msg/GoalStatus"
ACTION_ARR = "action_msgs/msg/GoalStatusArray"
ACTION_ST = "action_msgs/msg/GoalStatus"
FAMILY = {ACTIONLIB_ARR: ("actionlib", ACTIONLIB_ST), ACTION_ARR: ("action_msgs", ACTION_ST)}
# finding indicator -> constant name, per family (CANCELED does not exist in actionlib_msgs: D1)
TERMINAL = {"actionlib": {"ABORTED": "ABORTED"}, "action_msgs": {"ABORTED": "STATUS_ABORTED", "CANCELED": "STATUS_CANCELED"}}


class GoalStatusDetector(TierABase):
    detector_id = "tierA.goal_status"
    SUPPORTED = {ACTIONLIB_ARR: {}, ACTION_ARR: {}}

    def consts_type(self, name: str) -> str:
        return FAMILY[name][1]

    def _field_type_problem(self, codec, name):
        ft = (codec.types or {}).get(FAMILY[name][1], {}).get("status")
        if ft is not None and (ft.base not in ("uint8", "int8", "byte") or ft.array):
            return f"'status' is declared as {ft.base}{'[]' if ft.array else ''}, expected uint8/int8"
        return None

    def bind(self, topic_schemas, ev, config):
        ap = super().bind(topic_schemas, ev, config)
        self.first_seen: dict[tuple, bool] = {}
        for t, st in self.state.items():
            if FAMILY[st.msg_type][0] == "actionlib":
                self.cov_reasons.append(Reason(code="constant_not_defined", detail=f"{t}: CANCELED is not defined in actionlib_msgs/msg/GoalStatus"))
        return ap

    def _identity(self, family: str, el) -> tuple[str, object, str | None]:
        """-> (state, raw value, identity text for known goals)."""
        if family == "actionlib":
            gf, g = field(el, "goal_id")
            f, v = field(g, "id") if gf and (isinstance(g, dict) or _is_msg(g)) else (False, None)
            state = classify_text(f, v)
            return state, v, (v if state == KNOWN else None)
        gi_f, gi = field(el, "goal_info")
        gid_f, gid = field(gi, "goal_id") if gi_f and (isinstance(gi, dict) or _is_msg(gi)) else (False, None)
        f, v = field(gid, "uuid") if gid_f and (isinstance(gid, dict) or _is_msg(gid)) else (False, None)
        state = classify_uuid(f, v)
        hexid = None
        if state == KNOWN:
            seq = bytes(v) if isinstance(v, (bytes, bytearray)) else bytes(plain(list(v)))
            hexid = seq.hex()
        return state, v, hexid

    def _goal_stamp(self, family: str, el):
        f, parent = (field(el, "goal_id") if family == "actionlib" else field(el, "goal_info"))
        if not f or not (isinstance(parent, dict) or _is_msg(parent)):
            return None
        sf, stamp = field(parent, "stamp")
        if not sf or not (isinstance(stamp, dict) or _is_msg(stamp)):
            return None
        return {"sec": number_text(field(stamp, "sec")[1]), "nanosec": number_text(field(stamp, "nanosec")[1])}   # raw, never compared

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
        fam = FAMILY[st.msg_type][0]
        f, sl = field(root, "status_list")
        els = elements(sl) if f else None
        if els is None:
            L.malformed += 1
            return
        names = TERMINAL[fam]
        by_value = {v: k for k, v in st.effective.items()}
        occ = occ_of(view)
        mx = self.lim.max_text_bytes
        for i, el in enumerate(els):
            if not (isinstance(el, dict) or _is_msg(el)):
                L.malformed += 1
                continue
            sf, sv = field(el, "status")
            sv = strict_int(sv) if sf else None
            if sv is None:
                L.malformed += 1                                  # evaluation field wrong: not evaluated
                continue
            idstate, raw, ident = self._identity(fam, el)
            if idstate in ("absent", "malformed"):
                L.ident("goal_id.id" if fam == "actionlib" else "goal_info.goal_id.uuid")
            cname = by_value.get(sv)
            if cname is None:
                L.unrec(sv)
            known = idstate == KNOWN
            fkey = (view.topic, ident) if known else None
            first_time = known and fkey not in self.first_seen
            if known:
                self.first_seen[fkey] = True
            indicator = next((k for k, c in names.items() if c == cname), None)
            if indicator is None:
                continue
            fp = f"status_list[{i}].status"
            if known:
                key = (view.topic, "known", fam, ident, sv)
            else:
                key = (view.topic, "unknown", occ.artifact_ref, occ.index, i)
            g = self.collector.get(key)
            if g is None:
                text = None
                if fam == "actionlib":
                    tf, tv = field(el, "text")
                    text = tv if tf and isinstance(tv, str) else None
                g = self.collector.start(key, occ, {"status": sv, "indicator": indicator, "family": fam, "ident": ident, "idstate": idstate,
                                                    "raw": raw, "element_index": i, "stamp": self._goal_stamp(fam, el), "text": text,
                                                    "first_seen": first_time})
                if g is None:
                    continue
            raw_text = None if known else render_raw(idstate, raw, mx)
            self.collector.add(g, occ, lambda fp=fp, idstate=idstate, raw_text=raw_text: ObservationRef(
                artifact_ref=occ.artifact_ref, sha256=occ.sha256, topic=occ.topic, message_index=occ.index, order=occ.order,
                log_time_ns=occ.log_time_ns, field_path=fp, value=sv, identity_state=idstate, identity_raw=raw_text))
            if self.uses_builtin(st, view, (cname,)):
                g.notes["constants_from_builtin_table"] = None

    def finish(self, cov: TopicCoverage):
        findings = []
        mx = self.lim.max_text_bytes
        for g in self.collector.groups:
            m = g.meta
            st = self.state[g.first.topic]
            known = g.key[1] == "known"
            lens = []
            text = m["text"]
            if isinstance(text, str):
                text, o = truncate_text(text, mx)
                if o:
                    lens.append(o)
            observed = {"status": m["status"], "status_name": m["indicator"], "family": m["family"], "element_index": m["element_index"],
                        "goal_id": m["ident"], "goal_stamp": m["stamp"], "text": text}
            if not known:
                observed["goal_id_raw"] = render_raw(m["idstate"], m["raw"], mx)
            notes = [note(c, p) for c, p in sorted(g.notes.items())]
            if not known:
                notes.append(note("identity_unknown", {"identity_state": m["idstate"]}))
            if m["first_seen"]:
                notes.append(note("first_seen_may_precede_recording"))
            if lens:
                notes.append(note("text_truncated", {"original_length": max(lens)}))
            gk = ["known", m["family"], g.key[3], m["status"]] if known else ["unknown", g.key[2], g.key[3], g.key[4]]
            sev = severity_for(f"goal.{m['indicator']}")
            if m["indicator"] == "CANCELED":
                expl = (f"Status CANCELED ({m['status']}) was reported for a goal in the recorded status stream in {g.count} message(s); "
                        "status CANCELED is not by itself a failure indicator.")
            else:
                expl = f"Status ABORTED ({m['status']}) was reported for a goal in the recorded status stream in {g.count} message(s)."
            findings.append(self.base_finding(g, cov, message_type=st.msg_type, severity=sev, category="goal_status",
                                              field_path=g.obs[0].field_path if g.obs else None, template="status_list[*].status",
                                              group_key=gk, observed=observed, explanation=expl, notes=notes))
        return self.assemble(cov, findings)
