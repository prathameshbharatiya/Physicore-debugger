from tests.anomaly.tools.mutate import Mutant

T = "tests/anomaly/"
LOG = (T + "test_tier_a_log.py",)
DIAG = (T + "test_tier_a_diagnostics.py",)
IDT = (T + "test_identity.py",)
LP = "physicore_collector/anomaly/detectors/tier_a_log.py"
DP = "physicore_collector/anomaly/detectors/tier_a_diagnostics.py"
BP = "physicore_collector/anomaly/detectors/base.py"
IP = "physicore_collector/anomaly/identity.py"
AP = "physicore_collector/anomaly/access.py"

MUTANTS = [
    # log
    Mutant("log: INFO is a finding (TA-LOG-01)", LP, 'FINDING_LEVELS = ("WARN", "ERROR", "FATAL")', 'FINDING_LEVELS = ("INFO", "WARN", "ERROR", "FATAL")', LOG),
    Mutant("log: FATAL dropped (TA-LOG-01)", LP, 'FINDING_LEVELS = ("WARN", "ERROR", "FATAL")', 'FINDING_LEVELS = ("WARN", "ERROR")', LOG),
    Mutant("log: >= instead of set membership (TA-LOG-02)", LP, "        if lname is None:\n            L.unrec(lv)\n            return\n",
           "        if lname is None and lv >= 30:\n            lname = 'ERROR'\n        if lname is None:\n            L.unrec(lv)\n            return\n", LOG),
    Mutant("log: unrecognised not counted (TA-LOG-02)", LP, "            L.unrec(lv)\n", "            pass\n", LOG),
    Mutant("log: builtin ROS1 WARN wrong (TA-LOG-06)", "physicore_collector/anomaly/ros_constants.py", '"rosgraph_msgs/msg/Log": {"DEBUG": 1, "INFO": 2, "WARN": 4,', '"rosgraph_msgs/msg/Log": {"DEBUG": 1, "INFO": 2, "WARN": 30,', LOG),
    Mutant("log: string level coerced (TA-LOG-07)", AP, "    if isinstance(v, Decimal) and v.is_finite() and v.as_tuple().exponent == 0:\n        return int(v)\n",
           "    if isinstance(v, Decimal) and v.is_finite() and v.as_tuple().exponent == 0:\n        return int(v)\n    if isinstance(v, (str, float)):\n        return int(float(v))\n", LOG),
    Mutant("log: bool accepted as level (TA-LOG-07)", AP, "    if isinstance(v, bool):\n        return None\n    if isinstance(v, int):\n        return v\n    if isinstance(v, Decimal)", "    if isinstance(v, int):\n        return int(v)\n    if isinstance(v, Decimal)", LOG),
    Mutant("log: float level accepted (TA-LOG-07)", AP, "v.as_tuple().exponent == 0:", "v.as_tuple().exponent <= 0:", LOG),
    Mutant("log: malformed level still evaluated (TA-LOG-23f)", LP, "            L.malformed += 1                                     # evaluation field: not evaluated, counted\n            return\n", "            L.malformed += 1\n            lv = 40\n", LOG),
    Mutant("log: absent name suppresses finding (TA-LOG-23)", LP, '        if nstate in ("absent", "malformed"):\n            L.ident("name")', '        if nstate in ("absent", "malformed"):\n            L.ident("name")\n            return', LOG),
    Mutant("log: empty names merged (TA-LOG-20)", LP, 'key = (view.topic, "unknown", occ.artifact_ref, occ.index)', 'key = (view.topic, "unknown")', LOG),
    Mutant("log: absent name treated as shared identity (TA-LOG-23d)", LP, "        if nstate == KNOWN:\n            key =", "        if nstate in (KNOWN, 'absent'):\n            key =", LOG),
    Mutant("log: identity accounting degrades status (TA-LOG-23g)", BP, "        degraded = (not cov.complete) or L.undecodable or L.malformed or L.unrecognised", "        degraded = (not cov.complete) or L.undecodable or L.malformed or L.unrecognised or L.identity_malformed", LOG + DIAG),
    Mutant("log: logger name prefix-merged (TA-LOG-22)", LP, 'key = (view.topic, "known", name, lv, text_key)', 'key = (view.topic, "known", str(name)[:3], lv, text_key)', LOG),
    Mutant("log: text ignored in group key (TA-LOG-26)", LP, 'key = (view.topic, "known", name, lv, text_key)', 'key = (view.topic, "known", name, lv)', LOG),
    Mutant("log: level ignored in group key (TA-LOG-04)", LP, 'key = (view.topic, "known", name, lv, text_key)', 'key = (view.topic, "known", name, 0, text_key)', LOG),
    Mutant("log: embedded constants ignored (TA-LOG-14)", BP, "        for k, v in embedded.items():\n            if k in table and table[k] != v:", "        for k, v in embedded.items():\n            if False:", LOG),
    Mutant("log: text not truncated (TA-LOG-16)", "physicore_collector/anomaly/identity.py", "    if len(b) <= max_bytes:\n        return text, None", "    if True:\n        return text, None", LOG + IDT + DIAG),
    Mutant("log: stamp domain wrong (TA-LOG-18)", LP, 'clock_domain=f"payload_stamp:{view.topic}:{STAMP_FIELD[st.msg_type]}"', 'clock_domain="mcap_log_time"', LOG),
    Mutant("log: last observation uses first (TA-LOG-03)", BP, "g.last.log_time_ns if g.count > 1 else None)", "g.first.log_time_ns if g.count > 1 else None)", LOG),
    Mutant("log: ROS1 CDR not blind spot (TA-LOG-19)", BP, "        if name in self.JSON_ONLY and codecs:", "        if False:", LOG),
    Mutant("log: suppressed count not exact (TA-LOG-24)", BP, "        return len(self._suppressed)", "        return 1 if self._suppressed else 0", LOG + DIAG),
    Mutant("log: cap ignored (TA-LOG-24)", BP, "        if len(self.groups) >= self.max_findings:\n            self._suppressed.add(key)\n            return None", "        if False:\n            return None", LOG + DIAG),
    Mutant("log: id ignores artifacts (TA-LOG-25)", BP, "contributing_artifacts=arts, topic=topic", "contributing_artifacts=arts[:1], topic=topic", LOG + DIAG),
    Mutant("log: artifacts only from retained refs (TA-LOG-25)", BP, "        g.artifacts.add((occ.artifact_ref, occ.sha256))\n        if len(g.obs) < self.max_obs:\n            g.obs.append(obs())",
           "        if len(g.obs) < self.max_obs:\n            g.artifacts.add((occ.artifact_ref, occ.sha256))\n            g.obs.append(obs())", LOG),
    Mutant("log: retained observations unlimited (CS-15)", BP, "        if len(g.obs) < self.max_obs:\n            g.obs.append(obs())", "        g.obs.append(obs())", LOG),
    Mutant("log: occurrence count off by one (TA-LOG-03)", BP, "        g.count += 1\n        g.last = occ", "        g.count += 1 if g.count else 2\n        g.last = occ", LOG),
    Mutant("log: incomplete recording not degraded (TA-LOG-13)", BP, "        degraded = (not cov.complete) or", "        degraded = False or", LOG + DIAG),
    Mutant("log: no messages is no_anomaly", BP, "        if cov.messages_examined == 0:", "        if False:", LOG + DIAG),
    Mutant("log: severity mapping wrong", "physicore_collector/anomaly/severity.py", '"log.FATAL": ("critical"', '"log.FATAL": ("error"', LOG),
    # diagnostics
    Mutant("diag: OK is a finding (TA-DIAG-01)", DP, 'FINDING_LEVELS = ("WARN", "ERROR", "STALE")', 'FINDING_LEVELS = ("OK", "WARN", "ERROR", "STALE")', DIAG),
    Mutant("diag: STALE dropped (TA-DIAG-01)", DP, 'FINDING_LEVELS = ("WARN", "ERROR", "STALE")', 'FINDING_LEVELS = ("WARN", "ERROR")', DIAG),
    Mutant("diag: other level does not end run (TA-DIAG-03)", DP, "                if known:\n                    self.open.pop(key, None)", "                if False:\n                    self.open.pop(key, None)", DIAG),
    Mutant("diag: absence breaks at K+1 (TA-DIAG-04)", DP, "                if run.absent >= self.k:", "                if run.absent > self.k:", DIAG),
    Mutant("diag: absence breaks without K (TA-DIAG-04)", DP, "        if self.k is not None and not any_bad:", "        if not any_bad:", DIAG),
    Mutant("diag: hardware_id ignored in key (TA-DIAG-07)", DP, "key = (view.topic, name, hw) if known else None", "key = (view.topic, name) if known else None", DIAG),
    Mutant("diag: topic ignored in key (TA-DIAG-15)", DP, "key = (view.topic, name, hw) if known else None", "key = (name, hw) if known else None", DIAG),
    Mutant("diag: empty name merged (TA-DIAG-06)", DP, "gkey = (view.topic, \"unknown\", occ.artifact_ref, occ.index, i)", "gkey = (view.topic, \"unknown\")", DIAG),
    Mutant("diag: element index ignored (TA-DIAG-14)", DP, "gkey = (view.topic, \"unknown\", occ.artifact_ref, occ.index, i)", "gkey = (view.topic, \"unknown\", occ.artifact_ref, occ.index)", DIAG),
    Mutant("diag: empty hardware_id makes identity unknown (TA-DIAG-13)", IP, '    return KNOWN if isinstance(value, str) else MALFORMED', '    return KNOWN if isinstance(value, str) and value else MALFORMED', DIAG + IDT),
    Mutant("diag: absent name suppresses (TA-DIAG-17)", DP, '            if ns in ("absent", "malformed"):\n                L.ident("name")', '            if ns in ("absent", "malformed"):\n                L.ident("name")\n                continue', DIAG),
    Mutant("diag: malformed level not counted (TA-DIAG-09)", DP, "            if lv is None:\n                L.malformed += 1\n                any_bad = True", "            if lv is None:\n                any_bad = True", DIAG),
    Mutant("diag: severity STALE as error", "physicore_collector/anomaly/severity.py", '"diagnostic.STALE": ("warning"', '"diagnostic.STALE": ("error"', DIAG),
    Mutant("diag: distinct messages not tracked (TA-DIAG-05)", DP, "                if h not in m[\"distinct_hashes\"]:\n                    m[\"distinct_hashes\"].add(h)", "                if False:\n                    m[\"distinct_hashes\"].add(h)", DIAG),
    Mutant("diag: first values not retained (TA-DIAG-08)", DP, '                m["values_first"] = m["values_last"]', '                m["values_first"] = None', DIAG),
    Mutant("diag: bare status element index (TA-DIAG-11)", DP, "        bare = st.msg_type == STATUS", "        bare = False", DIAG),
    # identity module
    Mutant("identity: all-zero uuid known (TA-GOAL-16)", IP, "    return EMPTY if not any(octets) else KNOWN", "    return KNOWN", IDT),
    Mutant("identity: wrong-length uuid accepted", IP, "    if len(octets) != 16:\n        return MALFORMED", "    if False:\n        return MALFORMED", IDT),
    Mutant("identity: null name is empty not malformed", IP, "    if not isinstance(value, str):\n        return MALFORMED            # includes", "    if value is None:\n        return EMPTY\n    if not isinstance(value, str):\n        return MALFORMED            # includes", IDT + LOG),
    Mutant("identity: non-integer octets accepted", IP, "        if any(o is None for o in raw):\n            return MALFORMED", "        if False:\n            return MALFORMED", IDT),
    Mutant("identity: out-of-range octet accepted", IP, "        if any(not 0 <= o <= 255 for o in octets):\n            return MALFORMED", "        if False:\n            return MALFORMED", IDT),
]
