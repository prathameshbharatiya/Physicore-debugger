from tests.anomaly.tools.mutate import Mutant

T = "tests/anomaly/"
ST = (T + "test_tier_b_stamp_order.py",)
S = "physicore_collector/anomaly/detectors/tier_b_stamp_order.py"
A = "physicore_collector/anomaly/access.py"

MUTANTS = [
    Mutant("stamp: backward uses <= (STAMP-03)", S, "                if ns < part.prev_ns:", "                if ns <= part.prev_ns:", ST),
    Mutant("stamp: backward never reported (STAMP-02)", S, "                if ns < part.prev_ns:", "                if False:", ST),
    Mutant("stamp: duplicates not reported (STAMP-03)", S, "                elif ns == part.prev_ns:", "                elif False:", ST),
    Mutant("stamp: duplicate on greater (STAMP-01)", S, "                elif ns == part.prev_ns:", "                elif ns >= part.prev_ns:", ST),
    Mutant("stamp: prev not reset after regression (STAMP-04)", S, "            part.prev_ns, part.prev_occ, part.prev_ref = ns, occ, ref", "            if part.prev_ns is None or ns > part.prev_ns:\n                part.prev_ns, part.prev_occ, part.prev_ref = ns, occ, ref", ST),
    Mutant("stamp: run not closed on increase (STAMP-03)", S, "                else:\n                    self._close_run(part)\n            part.prev_ns", "                else:\n                    pass\n            part.prev_ns", ST),
    Mutant("stamp: run not closed on regression", S, "                    self._close_run(part)\n                    self._backward", "                    self._backward", ST),
    Mutant("stamp: zero skipped as unset (STAMP-05/06/24)", S, "            ns, label = view.stamp(f)\n            if ns is None:", "            ns, label = view.stamp(f)\n            if ns is None or ns == 0:", ST),
    Mutant("stamp: zero note dropped (STAMP-05)", S, '                    notes.append(note("zero_stamp_valid_not_assumed_unset"))', "                    pass", ST),
    Mutant("stamp: invalid not counted (STAMP-08)", S, "                ch.invalid += 1\n", "", ST),
    Mutant("stamp: invalid updates prev (STAMP-20)", S, "                for part in ch.parts.values():\n                    part.excluded_since_valid += 1\n                ch.excluded_total += 1\n                continue", "                for part in ch.parts.values():\n                    part.excluded_since_valid += 1\n                ns = 0\n                label = ''", ST),
    Mutant("stamp: invalid does not degrade status (STAMP-23)", S, '        if invalid or (self.chans and not evaluable):\n            return "blind_spot", reasons', '        if (self.chans and not evaluable):\n            return "blind_spot", reasons', ST),
    Mutant("stamp: exclusion note dropped (STAMP-20/21)", S, '            if m.get("excluded"):\n                notes.append(', '            if False:\n                notes.append(', ST),
    Mutant("stamp: structure-only note dropped", S, '            if ch.category == "structure_only":\n                notes.append(', '            if False:\n                notes.append(', ST),
    Mutant("stamp: rejected type evaluated (STAMP-09)", S, 'NOT_EVALUATED = ("rejected_type", ', 'NOT_EVALUATED = (', ST),
    Mutant("stamp: field_not_in_schema evaluated (STAMP-09)", S, '"field_not_in_schema", "schema_unresolved"', '"schema_unresolved"', ST),
    Mutant("stamp: partition ignored (STAMP-13)", S, "            pkey = tuple(sorted(pvals.items()))", "            pkey = ()", ST),
    Mutant("stamp: bad partition value evaluated (STAMP-13)", S, "                L.malformed += 1                                   # partition value missing or ill-typed: not evaluated by this detector\n                continue", "                pkey = ()", ST),
    Mutant("stamp: partition value bool accepted", S, "if not ok or isinstance(v, bool) or not isinstance(v, (str, int)):", "if not ok or not isinstance(v, (str, int)):", ST),
    Mutant("stamp: single valid message is no anomaly (STAMP-15)", S, "        if not enough:\n            return", "        if False:\n            return", ST),
    Mutant("stamp: clock domain wrong (STAMP-11)", S, '            dom = payload_stamp_domain(ch.topic, ch.field)', '            dom = payload_stamp_domain(ch.topic, "x")', ST),
    Mutant("stamp: backward magnitude wrong (STAMP-02)", S, 'mag = m["prev"] - m["cur"]', 'mag = m["cur"] - m["prev"]', ST),
    Mutant("stamp: duplicate count off (STAMP-03)", S, '"messages_in_run": g.count, "repeats": g.count - 1', '"messages_in_run": g.count - 1, "repeats": g.count - 1', ST),
    Mutant("stamp: prev message not a contributing artifact", S, "        g.artifacts.update({(part.prev_occ.artifact_ref, part.prev_occ.sha256), (occ.artifact_ref, occ.sha256)})", "        g.artifacts.update({(occ.artifact_ref, occ.sha256)})", ST),
    Mutant("stamp: private stamp parser (STAMP-19)", A, "            return _obs.header_stamp_ns(d.root, field_path, d.types, d.root_type), label", "            return None, label", ST),
    Mutant("stamp: json stamp accepts negative sec (STAMP-19)", A, "        return _obs.header_stamp_ns(d, field_path, None, None), label", "        r = _obs.header_stamp_ns(d, field_path, None, None)\n        return (r if r is not None else 0), label", ST),
    Mutant("stamp: labels cached across fields wrongly", A, "        key = (self._msg.topic, field_path)", "        key = (self._msg.topic, '')", ST),
]
