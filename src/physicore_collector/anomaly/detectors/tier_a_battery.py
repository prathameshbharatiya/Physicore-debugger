"""tierA.battery (DESIGN 3.4, D4): power_supply_health flags (7 values) and optional configured percentage/voltage thresholds.
`power_supply_status` and `present` are context only: they never create a finding."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from ..access import DecodeFailure, MessageView, exact_number, field, is_real, number_text, strict_int
from ..findings import ObservationRef, Reason, Threshold
from ..identity import EMPTY, KNOWN, classify_text, render_raw
from ..runner import TopicCoverage
from ..severity import severity_for
from .base import Group, TierABase, note, occ_of

TYPE = "sensor_msgs/msg/BatteryState"
PFX = "POWER_SUPPLY_HEALTH_"
UNHEALTHY = ("OVERHEAT", "DEAD", "OVERVOLTAGE", "UNSPEC_FAILURE", "COLD", "WATCHDOG_TIMER_EXPIRE", "SAFETY_TIMER_EXPIRE")
RECOGNISED = ("UNKNOWN", "GOOD") + UNHEALTHY


@dataclass
class Run:
    group: Group | None
    flag: object


class BatteryDetector(TierABase):
    detector_id = "tierA.battery"
    SUPPORTED = {TYPE: {}}

    def _field_type_problem(self, codec, name):
        for f in ("power_supply_health",):
            ft = (codec.types or {}).get(TYPE, {}).get(f)
            if ft is not None and (ft.base not in ("uint8", "byte") or ft.array):
                return f"'{f}' is declared as {ft.base}{'[]' if ft.array else ''}, expected uint8"
        return None

    def bind(self, topic_schemas, ev, config):
        ap = super().bind(topic_schemas, ev, config)
        self.open: dict[tuple, Run] = {}
        self.health_on = bool(self.params.get("health_flags", True))
        th = self.params.get("thresholds", {})
        self.pct = Fraction(th["percentage_below"]) if "percentage_below" in th else None
        self.volt = Fraction(th["voltage_below"]) if "voltage_below" in th else None
        self.pct_scale = th.get("percentage_scale")
        self.health_seen = 0
        self.health_unknown = 0
        self.run_seq = 0
        return ap

    def _ctx(self, root) -> dict:
        out = {}
        for f in ("power_supply_status", "present", "voltage", "percentage"):
            ok, v = field(root, f)
            out[f] = number_text(v) if ok else None
        return out

    def _run(self, view, occ, key, idkey, flag, gmeta, notes_extra=()):
        """Extend or start the run of `flag` for (idkey, class); returns the group (None when suppressed by the findings cap)."""
        run = self.open.get(idkey)
        if run is not None and run.flag == flag:
            return run.group, False
        self.run_seq += 1
        g = self.collector.start(key + (self.run_seq,), occ, gmeta)
        self.open[idkey] = Run(g, flag)
        return g, True

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
        occ = occ_of(view)
        mx = self.lim.max_text_bytes
        sf, ser = field(root, "serial_number")
        lf, loc = field(root, "location")
        ss, ls = classify_text(sf, ser), classify_text(lf, loc)
        if ss in ("absent", "malformed"):
            L.ident("serial_number")
        if ls in ("absent", "malformed"):
            L.ident("location")
        if ss in ("absent", "malformed") or ls in ("absent", "malformed"):
            idk, istate, iraw = None, (ss if ss in ("absent", "malformed") else ls), None
            iraw = "; ".join(f"{n}={render_raw(s_, v_, mx)}" for n, s_, v_ in (("serial_number", ss, ser), ("location", ls, loc)) if s_ in ("absent", "malformed"))
        elif ss == EMPTY and ls == EMPTY:
            idk, istate, iraw = (view.topic, "topic"), "empty", None           # approved exception: topic-level grouping
        else:
            idk, istate, iraw = (view.topic, ser, loc), "known", None
        topic_level = istate == "empty"
        base_state = istate

        def add(g, fp, value, vtext):
            def obs() -> ObservationRef:
                from ..findings import PayloadStampRef
                stamp = None
                sv, label = view.stamp("header.stamp")
                if sv is not None:
                    stamp = PayloadStampRef(value_ns=sv, clock_domain=f"payload_stamp:{view.topic}:header.stamp", stamp_type_check=label)
                return ObservationRef(artifact_ref=occ.artifact_ref, sha256=occ.sha256, topic=occ.topic, message_index=occ.index, order=occ.order,
                                      log_time_ns=occ.log_time_ns, field_path=fp, value=value if isinstance(value, (int, bool)) else None,
                                      value_repr=vtext, payload_stamp=stamp, identity_state=("empty" if topic_level else base_state),
                                      identity_raw=("" if topic_level else iraw))
            self.collector.add(g, occ, obs)
            g.meta["ctx_last"] = self._ctx(root)
            if g.count == 1:
                g.meta["ctx_first"] = g.meta["ctx_last"]

        meta_common = {"ser": ser if ss == KNOWN or ss == EMPTY else None, "loc": loc if ls == KNOWN or ls == EMPTY else None,
                       "istate": istate, "topic_level": topic_level, "iraw": iraw}

        # --- class 1: health flag (evaluation field: power_supply_health) ---
        if self.health_on:
            hf, hv = field(root, "power_supply_health")
            h = strict_int(hv) if hf else None
            if h is None:
                L.malformed += 1
            else:
                self.health_seen += 1
                by_value = {v: k[len(PFX):] for k, v in st.effective.items() if k.startswith(PFX)}
                hname = by_value.get(h)
                if hname is None or hname not in RECOGNISED:
                    L.unrec(h)
                    if idk is not None:
                        self.open.pop(idk + ("health",), None)
                elif hname == "UNKNOWN":
                    self.health_unknown += 1
                    if idk is not None:
                        self.open.pop(idk + ("health",), None)
                elif hname not in UNHEALTHY:
                    if idk is not None:
                        self.open.pop(idk + ("health",), None)
                else:
                    meta = {**meta_common, "kind": "health", "value": h, "name": hname}
                    if idk is None:
                        key = (view.topic, "unknown", "health", occ.artifact_ref, occ.index)
                        g = self.collector.start(key, occ, meta)
                    else:
                        g, _new = self._run(view, occ, (view.topic, "known", "health", idk[1:], h), idk + ("health",), h, meta)
                    if g is not None:
                        add(g, "power_supply_health", h, None)
                        if self.uses_builtin(st, view, (PFX + hname,)):
                            g.notes["constants_from_builtin_table"] = None

        # --- class 4: configured thresholds ---
        for kind, thr, fieldname in (("percentage", self.pct, "percentage"), ("voltage", self.volt, "voltage")):
            if thr is None:
                continue
            ff, fv = field(root, fieldname)
            if not ff or not is_real(fv):
                L.malformed += 1
                continue
            x = exact_number(fv)
            below = x is not None and x < thr                       # strict; NaN/Inf are never a threshold finding
            ik = None if idk is None else idk + (kind,)
            if not below:
                if ik is not None:
                    self.open.pop(ik, None)
                continue
            vtext = number_text(fv)
            meta = {**meta_common, "kind": kind, "value": vtext, "threshold": thr}
            if idk is None:
                g = self.collector.start((view.topic, "unknown", kind, occ.artifact_ref, occ.index), occ, meta)
            else:
                g, _new = self._run(view, occ, (view.topic, "known", kind, idk[1:]), ik, True, meta)
            if g is not None:
                add(g, fieldname, None, str(vtext))
                g.meta["value_last"] = str(vtext)
                if g.count == 1:
                    g.meta["value_first"] = str(vtext)

    def extra_status(self, cov, n_findings):
        reasons: list[Reason] = []
        degrade = False
        if self.health_unknown:
            reasons.append(Reason(code="health_unknown_messages", count=self.health_unknown))
            if self.health_on and self.health_seen == self.health_unknown and n_findings == 0:
                reasons.append(Reason(code="health_unknown_throughout"))
                degrade = True
        return reasons, degrade

    def finish(self, cov: TopicCoverage):
        findings = []
        mx = self.lim.max_text_bytes
        for g in self.collector.groups:
            m = g.meta
            kind = m["kind"]
            st = self.state[g.first.topic]
            lens = []

            def t(x):
                from ..identity import truncate_text
                if not isinstance(x, str):
                    return x
                y, o = truncate_text(x, mx)
                if o:
                    lens.append(o)
                return y
            observed = {"serial_number": t(m["ser"]), "location": t(m["loc"]), "context_first": m.get("ctx_first"), "context_last": m.get("ctx_last")}
            notes = [note(c, p) for c, p in sorted(g.notes.items())]
            if m["istate"] not in ("known", "empty"):
                notes.append(note("identity_unknown", {"identity_state": m["istate"]}))
            if m["topic_level"]:
                notes.append(note("battery_identity_fields_empty"))
            threshold = None
            if kind == "health":
                observed.update({"health": m["value"], "health_name": m["name"]})
                sev = severity_for("battery.health")
                cat_field = "power_supply_health"
                expl = (f"power_supply_health value {m['name']} ({m['value']}) was reported in {g.count} message(s) "
                        f"from log_time {g.first.log_time_ns} to {g.last.log_time_ns} ns; power_supply_status and present are context only.")
                gk_kind = ["health", m["value"]]
            else:
                observed.update({"value_first": m.get("value_first"), "value_last": m.get("value_last")})
                sev = severity_for("battery.threshold")
                cat_field = kind
                thr = m["threshold"]
                tv = str(thr.numerator) if thr.denominator == 1 else f"{thr.numerator}/{thr.denominator}"
                unit = self.pct_scale if kind == "percentage" else "as recorded (volt field, not converted)"
                threshold = Threshold(name=f"{kind}_below", value=tv, unit=unit, source="config")
                expl = (f"{kind} was strictly below the configured threshold {tv} ({unit}) in {g.count} message(s) "
                        f"from log_time {g.first.log_time_ns} to {g.last.log_time_ns} ns.")
                gk_kind = [kind, tv]
            if lens:
                notes.append(note("text_truncated", {"original_length": max(lens)}))
            known = g.key[1] == "known"
            if not known:
                gk = ["unknown", *gk_kind, g.key[3], g.key[4]]
            elif m["topic_level"]:
                gk = ["known", *gk_kind, "topic"]
            else:
                gk = ["known", *gk_kind, m["ser"], m["loc"]]
            f = self.base_finding(g, cov, message_type=st.msg_type, severity=sev, category="power_status", field_path=cat_field,
                                  template=cat_field, group_key=gk, observed=observed, explanation=expl, notes=notes)
            if threshold is not None:
                f = f.model_copy(update={"threshold": threshold})
            findings.append(f)
        return self.assemble(cov, findings)
