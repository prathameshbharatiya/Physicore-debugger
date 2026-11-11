from tests.anomaly.tools.mutate import Mutant

T = "tests/anomaly/"
AT = (T + "test_tier_b_array_length.py",)
NT = (T + "test_tier_b_non_finite.py",)
A = "physicore_collector/anomaly/detectors/tier_b_array_length.py"
N = "physicore_collector/anomaly/detectors/tier_b_non_finite.py"
B = "physicore_collector/anomaly/bitinfo.py"
SI = "physicore_collector/anomaly/schemaidx.py"

MUTANTS = [
    Mutant("arr: length equal also reported (ARR-01)", A, "                    if run.length == expected:\n                        continue", "                    if False:\n                        continue", AT),
    Mutant("arr: nothing reported (ARR-02)", A, "                    if run.length == expected:\n                        continue", "                    continue", AT),
    Mutant("arr: run not split on absence (ARR-11b)", A, "run.length == n and run.last_seq == seq - 1", "run.length == n", AT),
    Mutant("arr: runs never merge (ARR-03)", A, "run.length == n and run.last_seq == seq - 1", "False", AT),
    Mutant("arr: absent counted as zero (ARR-11)", A, '                if status == "absent":\n                    rs.absent += 1\n                    continue', '                if status == "absent":\n                    node, status = [], "ok"', AT),
    Mutant("arr: tie picks first (ARR-07)", A, "                if len(tied) > 1:", "                if False:", AT),
    Mutant("arr: min_obs boundary strict (ARR-08)", A, "if rs.observations < rs.min_obs:", "if rs.observations <= rs.min_obs:", AT),
    Mutant("arr: share boundary strict (ARR-08b)", A, "if Fraction(best, rs.observations) < rs.min_share:", "if Fraction(best, rs.observations) <= rs.min_share:", AT),
    Mutant("arr: share check dropped (ARR-08b)", A, "if Fraction(best, rs.observations) < rs.min_share:", "if False:", AT),
    Mutant("arr: baseline note dropped (ARR-06)", A, 'notes = [note("baseline_from_recording")] if source == "recording_baseline" else []', "notes = []", AT),
    Mutant("arr: baseline artifacts only the run's (ARR-06b)", A, 'arts = all_arts if source == "recording_baseline" else sorted(run.arts)', "arts = sorted(run.arts)", AT),
    Mutant("arr: config source mislabelled (ARR-02)", A, 'expected, source = rs.expected, "config"', 'expected, source = rs.expected, "recording_baseline"', AT),
    Mutant("arr: fixed-size evaluated (ARR-04)", SI, '"fixed" if br.isdigit() else "sequence"', '"sequence"', AT),
    Mutant("arr: wrong type not counted (ARR-10)", A, "                    rs.wrong_type += 1\n", "", AT),
    Mutant("arr: wildcard only first element (ARR-05)", A, '            for i, e in enumerate(els):\n                rec(e, steps[1:], f"{here}[{i}]")', '            for i, e in enumerate(els[:1]):\n                rec(e, steps[1:], f"{here}[{i}]")', AT),
    Mutant("arr: observation cap ignored (ARR-18)", A, "if len(run.obs) < cap:", "if True:", AT),
    Mutant("arr: truncation flag dropped (ARR-18)", A, "obs_truncated=run.count > len(g.obs)", "obs_truncated=False", AT),
    Mutant("arr: cap suppression inexact (ARR-17)", A, "                    if not self.collector.admit(key):\n                        continue", "                    if len(findings) >= self.lim.max_findings_per_detector:\n                        continue", AT),
    Mutant("arr: undecodable not counted (ARR-13)", A, "            self.ledger.undecodable += 1\n", "", AT),
    Mutant("arr: incomplete ignored in status (ARR-15)", A, "or not cov.complete or L.undecodable or L.malformed:", "or L.undecodable or L.malformed:", AT),
    Mutant("arr: absent optional yields insufficient only (ARR-11)", A, 'if not rs.observations:\n                self.rule_notes', 'if rs.observations < 2:\n                self.rule_notes', AT),
    # ---------------- non finite
    Mutant("nf: nan not detected (NF-01)", N, '        if v != v:\n            return "nan"', '        if False:\n            return "nan"', NT),
    Mutant("nf: inf sign swapped (NF-01)", N, '        if math.isinf(v):\n            return "inf" if v > 0 else "-inf"', '        if math.isinf(v):\n            return "-inf" if v > 0 else "inf"', NT),
    Mutant("nf: early exit after first offender (NF-10d)", N, "                ent[0] += 1", "                ent[0] += 1\n                break", NT),
    Mutant("nf: count capped at retention (NF-10)", N, "                ent[0] += 1\n", "                ent[0] += 1 if ent[0] < cap else 0\n", NT),
    Mutant("nf: message count wrong (NF-10b)", N, '                    g.meta["msgs"] += 1', '                    g.meta["msgs"] += 0', NT),
    Mutant("nf: runs not split (NF-11)", N, "cur.last_seq == seq - 1", "True", NT),
    Mutant("nf: every message its own run (NF-11)", N, "cur.last_seq == seq - 1", "False", NT),
    Mutant("nf: classes share a run (NF-11b)", N, "            base = (topic, tmpl, cls)", "            base = (topic, tmpl, 'x')", NT),
    Mutant("nf: include ignored (NF-12)", N, "        if self.inc and not any(r.match(path) for r in self.inc):\n            return False", "", NT),
    Mutant("nf: exclude ignored (NF-12)", N, "        return not any(r.match(path) for r in self.exc)", "        return True", NT),
    Mutant("nf: glob wildcard literal (NF-12)", N, 'r"\\[\\d+\\]" if p == "[*]"', 'r"\\[\\*\\]" if p == "[*]"', NT),
    Mutant("nf: glob no prefix cover (NF-12)", N, 'return re.compile(rf"^{rx}(?:[.\\[].*)?$")', 'return re.compile(rf"^{rx}$")', NT),
    Mutant("nf: null counted (NF-07)", N, '            elif is_json and leaf.kind == "none":\n                self.json_null += 1', '            elif is_json and leaf.kind == "none":\n                pass', NT),
    Mutant("nf: numeric string note dropped (NF-08)", N, "                self.numeric_strings += 1", "                pass", NT),
    Mutant("nf: lenient json off (NF-06)", N, '            if view.payload_kind == "json" and d.kind == "json_unparseable":', "            if False:", NT),
    Mutant("nf: f32 note dropped (NF-01)", N, '            if cls == "nan" and width == 32:', "            if False:", NT),
    Mutant("nf: too-large not reported (NF-15)", N, '            if self.fail_kinds.get("payload_too_large"):', "            if False:", NT),
    Mutant("nf: incomplete ignored (NF-19)", N, "        if not cov.complete or L.undecodable:\n            return", "        if L.undecodable:\n            return", NT),
    Mutant("nf: undecodable not counted (NF-06c)", N, "                self.ledger.undecodable += 1\n", "", NT),
    Mutant("bit: f64 reports wrong basis (NF-02)", B, '"reencoded_from_decoded_float64"', '"not_reported"', NT),
    Mutant("bit: f32 nan claims bits (NF-01)", B, 'bits_hex=None, bits_basis="not_guaranteed")', 'bits_hex="7fc00000", bits_basis="not_guaranteed")', NT),
    Mutant("bit: sign always zero (NF-02e)", B, "    return 1 if math.copysign(1.0, x) < 0 else 0", "    return 0", NT),
    Mutant("bit: self-check never fails (NF-02d)", B, "    return all(_f64_to_bits(_bits_to_f64(b)) == b for b in SELF_CHECK_PATTERNS)", "    return True", NT),
    Mutant("bit: f32 inf bits swapped (NF-01)", B, '"ff800000" if x < 0 else "7f800000"', '"7f800000" if x < 0 else "ff800000"', NT),
    Mutant("access: lenient parse accepts nothing (NF-06b)", "physicore_collector/anomaly/access.py", 'parse_constant=lambda tok: {"NaN": float("nan"), "Infinity": float("inf"), "-Infinity": float("-inf")}[tok]', 'parse_constant=lambda tok: {"NaN": float("nan"), "Infinity": float("inf"), "-Infinity": float("inf")}[tok]', NT),
    Mutant("nf: suppressed run not remembered (NF-18b)", N, "                self.open[base] = OpenRun(g, seq)", "                if g is not None:\n                    self.open[base] = OpenRun(g, seq)", NT),
]
