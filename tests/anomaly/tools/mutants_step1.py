from tests.anomaly.tools.mutate import Mutant

F = "physicore_collector/anomaly/findings.py"
C = "physicore_collector/anomaly/config.py"
T = "tests/anomaly/"
ID = (T + "test_findings_schema.py",)
CFG = (T + "test_config.py",)

MUTANTS = [
    Mutant("id ignores contributing artifacts (CS-04j m1)", F, '"contributing_artifacts": [{"artifact_ref": r, "sha256": s} for r, s in arts],', '"contributing_artifacts": [],', ID),
    Mutant("id uses only the first artifact (CS-04j m2)", F, 'for r, s in arts],', 'for r, s in arts[:1]],', ID),
    Mutant("id uses path instead of digest (CS-04j m3)", F, '{"artifact_ref": r, "sha256": s} for r, s in arts', '{"artifact_ref": r, "sha256": r} for r, s in arts', ID),
    Mutant("id artifact list unsorted/not unique (CS-04f)", F, "arts = sorted(set((str(r), str(s)) for r, s in contributing_artifacts))", "arts = [(str(r), str(s)) for r, s in contributing_artifacts]", ID),
    Mutant("id ignores first message index", F, '"first_observation": {"artifact_ref": first_artifact_ref, "message_index": first_message_index},', '"first_observation": {"artifact_ref": first_artifact_ref},', ID),
    Mutant("id ignores detector version", F, '"detector_id": detector_id, "detector_version": detector_version,', '"detector_id": detector_id,', ID),
    Mutant("source not required sorted (CS-04a)", F, 'if keys != sorted(set(keys)):', 'if False:', ID),
    Mutant("explanation causal check removed (W-01)", F, "        if bad:\n            raise ValueError(f\"explanation uses causal wording", "        if False:\n            raise ValueError(f\"explanation uses causal wording", ID),
    Mutant("duplicate ids allowed (CS-05)", F, "if len(ids) != len(set(ids)):", "if False:", ID),
    Mutant("canonical json accepts floats", F, "    _reject_floats(obj)\n    return json.dumps(obj, sort_keys=True, separators", "    return json.dumps(obj, sort_keys=True, separators", ID),
    Mutant("report json not sorted", F, "sort_keys=True, indent=2", "sort_keys=False, indent=2", ID),
    Mutant("uncertainty vocabulary open", F, "if v not in UNCERTAINTY_CODES:", "if False:", ID),
    Mutant("severity_basis open", F, "if v not in SEVERITY_BASES:", "if False:", ID),
    Mutant("goal severity basis wrong (CS-06a)", "physicore_collector/anomaly/severity.py", '"goal.ABORTED": ("error", "detector_policy")', '"goal.ABORTED": ("error", "source_declared_level")', (T + "test_report_skeleton.py",)),
    Mutant("tier B basis wrong (CS-06)", "physicore_collector/anomaly/severity.py", '"tierB": ("notice", "detector_policy")', '"tierB": ("warning", "detector_policy")', (T + "test_report_skeleton.py",)),
    Mutant("battery threshold basis wrong (CS-06)", "physicore_collector/anomaly/severity.py", '"battery.threshold": ("warning", "config")', '"battery.threshold": ("warning", "detector_policy")', (T + "test_report_skeleton.py",)),
    Mutant("late-message limitation dropped (CS-19)", "physicore_collector/anomaly/report.py", '    "late-message detection is not performed (deferred)",\n', '', (T + "test_report_skeleton.py",)),
    Mutant("confidence field added (CS-07)", F, "    occurrence_count: StrictInt\n    observations: list[ObservationRef]", "    occurrence_count: StrictInt\n    confidence: StrictInt = 0\n    observations: list[ObservationRef]", ID),
    Mutant("unknown config keys accepted (CS-08/27)", C, "        for k in sorted(str(k) for k in m if k not in allowed):", "        for k in []:", CFG),
    Mutant("bool accepted as int (CS-10)", C, "return type(v) is int  # bool is rejected", "return isinstance(v, int)", CFG),
    Mutant("float ratio accepted (CS-11)", C, "if not isinstance(v, str) or not _DEC.fullmatch(v):", "if isinstance(v, str) and not _DEC.fullmatch(v):", CFG),
    Mutant("zero ratio accepted (CS-25)", C, "        if f <= 0:", "        if f < 0:", CFG),
    Mutant("both rate ratios omitted accepted (CS-21)", C, 'if "low_ratio" not in m and "high_ratio" not in m:', 'if False:', CFG),
    Mutant("low_ratio made mandatory (CS-23)", C, 'lo = v.ratio(m, "low_ratio", p, required=False)', 'lo = v.ratio(m, "low_ratio", p, required=True)', CFG),
    Mutant("high_ratio made mandatory (CS-22)", C, 'hi = v.ratio(m, "high_ratio", p, required=False)', 'hi = v.ratio(m, "high_ratio", p, required=True)', CFG),
    Mutant("missing required param tolerated (CS-09)", C, '                self.bad(f"{path}.{key}", "required parameter is missing (no default exists)")\n            return None\n        v = m[key]\n        if not _is_int(v):', '                pass\n            return None\n        v = m[key]\n        if not _is_int(v):', CFG),
    Mutant("limits not required (CS-20)", C, 'lm = v.mapping(top.get("limits"), "limits") if "limits" in top else (v.bad("limits", "required section is missing (no defaults exist)") or None)', 'lm = v.mapping(top.get("limits"), "limits") if "limits" in top else {"max_retained_observations": 1, "max_text_bytes": 1, "max_findings_per_detector": 1}', CFG),
    Mutant("tier B topics auto accepted (CS-13)", C, '    out: dict[str, Any] = {"topics": v.str_list(m, "topics", p, required=True)}\n    out["min_intervals"]', '    out: dict[str, Any] = {"topics": m.get("topics")}\n    out["min_intervals"]', CFG),
    Mutant("config hash depends on ratio spelling (CS-12)", C, "def _fr(f: Fraction) -> str:\n    return f\"{f.numerator}/{f.denominator}\"", "def _fr(f: Fraction) -> str:\n    return str(float(f))", CFG),
    Mutant("late_message accepted as detector (CS-18)", C, '"tierB.array_length": _val_array, "tierB.non_finite": _val_nonfinite,', '"tierB.array_length": _val_array, "tierB.non_finite": _val_nonfinite, "tierB.late_message": _val_nonfinite,', CFG),
    Mutant("percentage scale not required (CS-30)", C, 'if "percentage_scale" not in t:\n                    v.bad(', 'if False:\n                    v.bad(', CFG),
    Mutant("empty array rules accepted (CS-30)", C, "if not isinstance(rules, list) or not rules:", "if not isinstance(rules, list):", CFG),
    Mutant("array rule needs not exactly one (CS-30)", C, "if has_exp == has_base:", "if has_exp and has_base:", CFG),
    Mutant("findings change default exit (X-19a)", "physicore_collector/anomaly/exitcodes.py", "if fail_on_findings and finding_count >= 1:", "if finding_count >= 1:", (T + "test_exitcodes.py",)),
    Mutant("fail-on-findings flag ignored (X-19b)", "physicore_collector/anomaly/exitcodes.py", "if fail_on_findings and finding_count >= 1:", "if False:", (T + "test_exitcodes.py",)),
    Mutant("invalid config exit code wrong (X-19d)", "physicore_collector/anomaly/exitcodes.py", "EXIT_INVALID_CONFIG = 3", "EXIT_INVALID_CONFIG = 1", (T + "test_exitcodes.py",)),
    Mutant("not inspectable exit code wrong (X-19f)", "physicore_collector/anomaly/exitcodes.py", "EXIT_NOT_INSPECTABLE = 2", "EXIT_NOT_INSPECTABLE = 3", (T + "test_exitcodes.py",)),
    Mutant("evidence partial ignored (X-19g)", "physicore_collector/anomaly/exitcodes.py", "    if inventory_code == 1:\n        reasons.append", "    if False:\n        reasons.append", (T + "test_exitcodes.py",)),
    Mutant("cross-domain relation allowed (STAMP-17)", "physicore_collector/anomaly/clock.py", "        if other.domain != self.domain:\n            raise ClockDomainError", "        if False:\n            raise ClockDomainError", (T + "test_clock.py",)),
]
