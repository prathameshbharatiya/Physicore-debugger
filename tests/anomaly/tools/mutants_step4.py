from tests.anomaly.tools.mutate import Mutant

T = "tests/anomaly/"
GOAL = (T + "test_tier_a_goal_status.py",)
BAT = (T + "test_tier_a_battery.py",)
IDT = (T + "test_identity.py",)
GP = "physicore_collector/anomaly/detectors/tier_a_goal_status.py"
BP = "physicore_collector/anomaly/detectors/tier_a_battery.py"
AP = "physicore_collector/anomaly/access.py"

MUTANTS = [
    # goal status
    Mutant("goal: actionlib CANCELED invented (D1)", GP, '"actionlib": {"ABORTED": "ABORTED"}', '"actionlib": {"ABORTED": "ABORTED", "CANCELED": "REJECTED"}', GOAL),
    Mutant("goal: actionlib PREEMPTED treated as canceled (D1)", GP, '"actionlib": {"ABORTED": "ABORTED"}', '"actionlib": {"ABORTED": "ABORTED", "CANCELED": "PREEMPTED"}', GOAL),
    Mutant("goal: action_msgs CANCELED dropped", GP, '"action_msgs": {"ABORTED": "STATUS_ABORTED", "CANCELED": "STATUS_CANCELED"}', '"action_msgs": {"ABORTED": "STATUS_ABORTED"}', GOAL),
    Mutant("goal: ABORTED dropped (action_msgs)", GP, '"action_msgs": {"ABORTED": "STATUS_ABORTED", "CANCELED"', '"action_msgs": {"XABORTED": "STATUS_ABORTED", "CANCELED"', GOAL),
    Mutant("goal: actionlib builtin ABORTED value wrong", "physicore_collector/anomaly/ros_constants.py", '"SUCCEEDED": 3, "ABORTED": 4, "REJECTED": 5', '"SUCCEEDED": 3, "ABORTED": 6, "REJECTED": 5', GOAL),
    Mutant("goal: action_msgs builtin ABORTED value wrong", "physicore_collector/anomaly/ros_constants.py", '"STATUS_CANCELED": 5, "STATUS_ABORTED": 6', '"STATUS_CANCELED": 5, "STATUS_ABORTED": 4', GOAL),
    Mutant("goal: status ignored in key (TA-GOAL-09)", GP, 'key = (view.topic, "known", fam, ident, sv)', 'key = (view.topic, "known", fam, ident)', GOAL),
    Mutant("goal: identity ignored in key (TA-GOAL-06)", GP, 'key = (view.topic, "known", fam, ident, sv)', 'key = (view.topic, "known", fam, sv)', GOAL),
    Mutant("goal: grouped per message not per goal (TA-GOAL-04)", GP, 'key = (view.topic, "known", fam, ident, sv)', 'key = (view.topic, "known", fam, ident, sv, occ.index)', GOAL),
    Mutant("goal: unknown identities merged (TA-GOAL-07/16)", GP, 'key = (view.topic, "unknown", occ.artifact_ref, occ.index, i)', 'key = (view.topic, "unknown", fam)', GOAL),
    Mutant("goal: element index ignored (TA-GOAL-15)", GP, 'key = (view.topic, "unknown", occ.artifact_ref, occ.index, i)', 'key = (view.topic, "unknown", occ.artifact_ref, occ.index)', GOAL),
    Mutant("goal: first-seen flag always set (TA-GOAL-05)", GP, "first_time = known and fkey not in self.first_seen", "first_time = known", GOAL),
    Mutant("goal: first-seen flag never set (TA-GOAL-08)", GP, "first_time = known and fkey not in self.first_seen", "first_time = False", GOAL),
    Mutant("goal: first-seen only tracks terminal goals (TA-GOAL-05)", GP, "            if known:\n                self.first_seen[fkey] = True\n            indicator", "            indicator", GOAL),
    Mutant("goal: bad uuid suppresses finding (TA-GOAL-19)", GP, '            if idstate in ("absent", "malformed"):\n                L.ident(', '            if idstate in ("absent", "malformed"):\n                continue\n                L.ident(', GOAL),
    Mutant("goal: malformed uuid not counted (TA-GOAL-19)", GP, '                L.ident("goal_id.id" if fam == "actionlib" else "goal_info.goal_id.uuid")', '                pass', GOAL),
    Mutant("goal: status wrong type evaluated (TA-GOAL-21)", GP, "            if sv is None:\n                L.malformed += 1                                  # evaluation field wrong: not evaluated\n                continue", "            if sv is None:\n                sv = 6", GOAL),
    Mutant("goal: uuid hex uppercase (TA-GOAL-10)", GP, "            hexid = seq.hex()", "            hexid = seq.hex().upper()", GOAL),
    Mutant("goal: all-zero uuid known (TA-GOAL-16)", "physicore_collector/anomaly/identity.py", "    return EMPTY if not any(octets) else KNOWN", "    return KNOWN", GOAL + IDT),
    Mutant("goal: wrong severity for CANCELED", "physicore_collector/anomaly/severity.py", '"goal.CANCELED": ("notice"', '"goal.CANCELED": ("error"', GOAL),
    Mutant("goal: wrong basis for ABORTED (CS-06a)", "physicore_collector/anomaly/severity.py", '"goal.ABORTED": ("error", "detector_policy")', '"goal.ABORTED": ("error", "source_declared_level")', GOAL),
    Mutant("goal: CANCELED wording lost (TA-GOAL-14)", GP, "status CANCELED is not by itself a failure indicator.", "status CANCELED reported.", GOAL),
    Mutant("goal: constant_not_defined note dropped (D1)", GP, 'self.cov_reasons.append(Reason(code="constant_not_defined"', 'self.cov_reasons.append(Reason(code="constant_defined"', GOAL),
    Mutant("goal: goal stamp compared/normalised", GP, 'return {"sec": number_text(field(stamp, "sec")[1]), "nanosec": number_text(field(stamp, "nanosec")[1])}', 'return {"sec": abs(number_text(field(stamp, "sec")[1]) or 0), "nanosec": number_text(field(stamp, "nanosec")[1])}', GOAL),
    Mutant("goal: unrecognised status classified", GP, "            if cname is None:\n                L.unrec(sv)", "            if cname is None:\n                cname = 'STATUS_ABORTED'", GOAL),
    Mutant("goal: contributing artifacts not tracked across segments (TA-GOAL-18)", "physicore_collector/anomaly/detectors/base.py", "        g.artifacts.add((occ.artifact_ref, occ.sha256))\n", "", GOAL + BAT),
    # battery
    Mutant("bat: DEAD dropped (TA-BAT-01)", BP, 'UNHEALTHY = ("OVERHEAT", "DEAD", ', 'UNHEALTHY = ("OVERHEAT", ', BAT),
    Mutant("bat: GOOD is a finding (TA-BAT-01)", BP, 'UNHEALTHY = ("OVERHEAT", "DEAD", ', 'UNHEALTHY = ("GOOD", "OVERHEAT", "DEAD", ', BAT),
    Mutant("bat: UNKNOWN health not counted (TA-BAT-04)", BP, '                elif hname == "UNKNOWN":\n                    self.health_unknown += 1', '                elif hname == "UNKNOWN":\n                    pass', BAT),
    Mutant("bat: SAFETY_TIMER dropped (TA-BAT-01)", BP, ', "SAFETY_TIMER_EXPIRE")\nRECOGNISED', ')\nRECOGNISED', BAT),
    Mutant("bat: health constant wrong in builtin table", "physicore_collector/anomaly/ros_constants.py", '"POWER_SUPPLY_HEALTH_OVERHEAT": 2', '"POWER_SUPPLY_HEALTH_OVERHEAT": 3', BAT),
    Mutant("bat: UNKNOWN throughout not degraded (TA-BAT-04)", BP, "                degrade = True", "                degrade = False", BAT),
    Mutant("bat: UNKNOWN counted wrongly", BP, "                    self.health_unknown += 1", "                    self.health_unknown += 0", BAT),
    Mutant("bat: some-unknown degrades (TA-BAT-04)", BP, "self.health_seen == self.health_unknown and n_findings == 0", "self.health_unknown and n_findings == 0", BAT),
    Mutant("bat: status creates finding (TA-BAT-03)", BP, '        # --- class 1: health flag', '        if idk is not None and strict_int(field(root, "power_supply_status")[1]) == 3:\n            _g = self.collector.start((view.topic, "known", "health", idk[1:], 99, occ.index), occ, {**meta_common, "kind": "health", "value": 99, "name": "OVERHEAT"})\n            if _g is not None:\n                add(_g, "power_supply_status", 3, None)\n        # --- class 1: health flag', BAT),
    Mutant("bat: present=false creates finding (TA-BAT-07)", BP, '        # --- class 1: health flag', '        if idk is not None and field(root, "present")[1] is False:\n            _g = self.collector.start((view.topic, "known", "health", idk[1:], 99, occ.index), occ, {**meta_common, "kind": "health", "value": 99, "name": "OVERHEAT"})\n            if _g is not None:\n                add(_g, "present", None, None)\n        # --- class 1: health flag', BAT),
    Mutant("bat: threshold non-strict (TA-BAT-08)", BP, "below = x is not None and x < thr", "below = x is not None and x <= thr", BAT),
    Mutant("bat: NaN counted as below (TA-BAT-08)", BP, "below = x is not None and x < thr", "below = x is None or x < thr", BAT),
    Mutant("bat: threshold floats compared inexactly", BP, "below = x is not None and x < thr", "below = x is not None and float(x) < float(thr) + 1e-9", BAT),
    Mutant("bat: threshold basis wrong (CS-06)", "physicore_collector/anomaly/severity.py", '"battery.threshold": ("warning", "config")', '"battery.threshold": ("warning", "detector_policy")', BAT),
    Mutant("bat: threshold unit lost", BP, 'unit = self.pct_scale if kind == "percentage"', 'unit = "x" if kind == "percentage"', BAT),
    Mutant("bat: threshold run not broken", BP, "            if not below:\n                if ik is not None:\n                    self.open.pop(ik, None)\n                continue", "            if not below:\n                continue", BAT),
    Mutant("bat: flag change keeps run (TA-BAT-05)", BP, "        if run is not None and run.flag == flag:", "        if run is not None:", BAT),
    Mutant("bat: good message keeps run", BP, "                elif hname not in UNHEALTHY:\n                    if idk is not None:\n                        self.open.pop(idk + (\"health\",), None)", "                elif hname not in UNHEALTHY:\n                    pass", BAT),
    Mutant("bat: serial ignored in identity (TA-BAT-12)", BP, "idk, istate, iraw = (view.topic, ser, loc), \"known\", None", "idk, istate, iraw = (view.topic, None, loc), \"known\", None", BAT),
    Mutant("bat: both-empty exception removed (TA-BAT-13)", BP, "        elif ss == EMPTY and ls == EMPTY:\n            idk, istate, iraw = (view.topic, \"topic\"), \"empty\", None", "        elif False:\n            idk, istate, iraw = (view.topic, \"topic\"), \"empty\", None", BAT),
    Mutant("bat: exception note dropped (TA-BAT-13)", BP, '            if m["topic_level"]:\n                notes.append(note("battery_identity_fields_empty"))', '            if False:\n                notes.append(note("battery_identity_fields_empty"))', BAT),
    Mutant("bat: exception applied to one empty field (TA-BAT-16)", BP, "        elif ss == EMPTY and ls == EMPTY:", "        elif ss == EMPTY or ls == EMPTY:", BAT),
    Mutant("bat: absent serial treated as shared identity (TA-BAT-15)", BP, '        if ss in ("absent", "malformed") or ls in ("absent", "malformed"):\n            idk, istate', '        if False:\n            idk, istate', BAT),
    Mutant("bat: absent serial suppresses health (TA-BAT-15)", BP, '        if ss in ("absent", "malformed"):\n            L.ident("serial_number")', '        if ss in ("absent", "malformed"):\n            L.ident("serial_number")\n            return', BAT),
    Mutant("bat: malformed health evaluated (TA-BAT-10)", BP, "            if h is None:\n                L.malformed += 1", "            if h is None:\n                h = 2", BAT),
    Mutant("bat: threshold wrong type not counted (TA-BAT-10)", BP, "            if not ff or not is_real(fv):\n                L.malformed += 1", "            if not ff or not is_real(fv):\n                pass", BAT),
    Mutant("bat: bool accepted as number", AP, "    if isinstance(v, bool):\n        return False\n    if isinstance(v, (int, float, Decimal)):\n        return True", "    if isinstance(v, (int, float, Decimal)):\n        return True", BAT),
    Mutant("bat: context first lost (TA-BAT-05)", BP, '            if g.count == 1:\n                g.meta["ctx_first"] = g.meta["ctx_last"]', '            if False:\n                g.meta["ctx_first"] = g.meta["ctx_last"]', BAT),
    Mutant("bat: health_flags option ignored", BP, 'self.health_on = bool(self.params.get("health_flags", True))', 'self.health_on = True', BAT),
    Mutant("bat: embedded constants ignored", "physicore_collector/anomaly/detectors/base.py", "        for k, v in embedded.items():\n            if k in table and table[k] != v:", "        for k, v in embedded.items():\n            if False:", BAT + GOAL),
]
