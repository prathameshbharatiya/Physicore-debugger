from tests.localization.tools.mutate import Mutant

I = "physicore_collector/localization/intervals.py"
R = "physicore_collector/localization/report.py"
V = "physicore_collector/localization/vocabulary.py"
T = "tests/localization/"
INT = (T + "test_int.py",)
REP = (T + "test_report_base.py",)

MUTANTS = [
    # ---- MUT-INT
    Mutant("precedes: < -> <=", I, "        return self.upper_ns < other.lower_ns\n\n    def order", "        return self.upper_ns <= other.lower_ns\n\n    def order", INT),
    Mutant("order: before uses <=", I, "        if self.upper_ns < other.lower_ns:\n            return BEFORE", "        if self.upper_ns <= other.lower_ns:\n            return BEFORE", INT),
    Mutant("order: after uses <=", I, "        if other.upper_ns < self.lower_ns:\n            return AFTER", "        if other.upper_ns <= self.lower_ns:\n            return AFTER", INT),
    Mutant("order: before/after swapped", I, "            return BEFORE\n        if other.upper_ns < self.lower_ns:\n            return AFTER", "            return AFTER\n        if other.upper_ns < self.lower_ns:\n            return BEFORE", INT),
    Mutant("order: unordered returned as before", I, "        return UNORDERED\n\n    def unordered_with", "        return BEFORE\n\n    def unordered_with", INT),
    Mutant("min_separation uses lower-lower", I, "        return other.lower_ns - self.upper_ns", "        return other.lower_ns - self.lower_ns", INT),
    Mutant("min_separation off by one", I, "        return other.lower_ns - self.upper_ns", "        return other.lower_ns - self.upper_ns + 1", INT),
    Mutant("max_separation uses upper-upper", I, "        return other.upper_ns - self.lower_ns", "        return other.upper_ns - self.upper_ns", INT),
    Mutant("max_separation reversed sign", I, "        return other.upper_ns - self.lower_ns", "        return self.lower_ns - other.upper_ns", INT),
    Mutant("domain check dropped in precedes/order", I, "        if other.domain != self.domain:\n            raise ClockDomainError", "        if False:\n            raise ClockDomainError", INT),
    Mutant("domain check compares nothing (always raises)", I, "        if other.domain != self.domain:", "        if other.domain == self.domain:", INT),
    Mutant("lower>upper allowed", I, "        if self.lower_ns > self.upper_ns:", "        if False:", INT),
    Mutant("lower>=upper rejected (points forbidden)", I, "        if self.lower_ns > self.upper_ns:", "        if self.lower_ns >= self.upper_ns:", INT),
    Mutant("bool accepted as int", I, "    return type(v) is int ", "    return isinstance(v, (int, float)) ", INT),
    Mutant("type check dropped", I, "        if not (_is_int(self.lower_ns) and _is_int(self.upper_ns)):", "        if False:", INT),
    Mutant("empty domain allowed", I, "        if not isinstance(self.domain, str) or not self.domain:", "        if not isinstance(self.domain, str):", INT),
    Mutant("width off by one", I, "        return self.upper_ns - self.lower_ns\n\n    def precedes", "        return self.upper_ns - self.lower_ns + 1\n\n    def precedes", INT),
    Mutant("point() sets wrong upper", I, "        return cls(domain, ns, ns)", "        return cls(domain, ns, ns + 1)", INT),
    Mutant("other type check dropped", I, "        if not isinstance(other, Interval):\n            raise TypeError", "        if False:\n            raise TypeError", INT),
    Mutant("unordered_with inverted", I, "        return self.order(other) == UNORDERED", "        return self.order(other) != UNORDERED", INT),
    # ---- report primitives / wording
    Mutant("derived order 'before' check removed", R, '        if self.relation == "before" and not self.earlier.to_interval().precedes(self.later.to_interval()):', '        if False:', REP),
    Mutant("derived order 'unordered' check removed", R, '        if self.relation == "unordered" and self.earlier.to_interval().order(self.later.to_interval()) != "unordered":', '        if False:', REP),
    Mutant("rule id open", R, "        if v not in RULE_IDS:", "        if False:", REP),
    Mutant("relation vocabulary open", R, "        if v not in ORDER_RELATIONS:", "        if False:", REP),
    Mutant("interval model bounds unchecked", R, "        if self.lower_ns > self.upper_ns:\n            raise ValueError(\"interval lower bound exceeds upper bound\")", "        pass", REP),
    Mutant("strict base allows extras", R, 'model_config = ConfigDict(extra="forbid", strict=True, frozen=False)', 'model_config = ConfigDict(extra="allow", strict=True, frozen=False)', REP),
    Mutant("digest pattern not enforced", R, "        if not _SHA.fullmatch(v):", "        if False:", REP),
    Mutant("declared edge id may be blank", R, "        if not v.strip():", "        if False:", REP),
    Mutant("float finder blind to floats", R, "return [p for p, v, _ in walk(obj) if isinstance(v, float)]", "return []", REP),
    Mutant("canonical json not sorted", R, "return json.dumps(obj, sort_keys=True, separators=(\",\", \":\"), ensure_ascii=False)", "return json.dumps(obj, sort_keys=False, separators=(\",\", \":\"), ensure_ascii=False)", REP),
    Mutant("canonical json accepts floats", R, "    fl = find_floats(obj)\n    if fl:\n        raise TypeError(f\"floats are not allowed in M3d output (at {fl[0]})\")\n    return json.dumps(obj, sort_keys=True, separators", "    return json.dumps(obj, sort_keys=True, separators", REP),
    Mutant("report json keeps None values", R, 'model.model_dump(mode="json", exclude_none=True)', 'model.model_dump(mode="json", exclude_none=False)', REP),
    Mutant("walk misses list items", R, "        elif isinstance(v, (list, tuple)):", "        elif False:", REP),
    Mutant("forbidden key finder blind", R, "return [p for p, _v, k in walk(obj) if k is not None and forbidden_key_words(k)]", "return []", REP),
    Mutant("forbidden term list loses M3d additions", V, "M3D_FORBIDDEN_TERMS = (\"origin\",", "M3D_FORBIDDEN_TERMS = (\"origin_\",", REP),
    Mutant("propagation allowed everywhere", V, "    if _PROPAGATION_RE.search(stripped):\n        hits.add(\"propagation\")", "    pass", REP),
    Mutant("label token not exempt", V, "    stripped = text\n    for tok in ALLOWED_TOKENS:\n        stripped = stripped.replace(tok, \" \")", "    stripped = text", REP),
    Mutant("forbidden wording case sensitive", V, "+ r\")(?![A-Za-z])\", re.IGNORECASE)\n_PROPAGATION_RE", "+ r\")(?![A-Za-z])\")\n_PROPAGATION_RE", REP),
    Mutant("forbidden wording matches inside words", V, "_FORBIDDEN_RE = re.compile(r\"(?<![A-Za-z])(\"", "_FORBIDDEN_RE = re.compile(r\"(\"", REP),
    Mutant("hypothesis templates grow to four", V, "HYPOTHESIS_TEMPLATES = (\"propagation_along_declared_edge\", \"common_or_independent_origin_not_excluded\", \"observation_or_timing_artifact_not_excluded\")", "HYPOTHESIS_TEMPLATES = (\"propagation_along_declared_edge\", \"common_or_independent_origin_not_excluded\", \"observation_or_timing_artifact_not_excluded\", \"most_likely_cause\")", REP),
    Mutant("path_depth default changed", V, '"path_depth": 32', '"path_depth": 33', REP),
    Mutant("chains_reported default changed", V, '"chains_reported": 1_000', '"chains_reported": 1_001', REP),
    Mutant("statement kinds gain 'inferred'", V, 'STATEMENT_KINDS = ("observed", "derived_order", "declared")', 'STATEMENT_KINDS = ("observed", "derived_order", "declared", "inferred")', REP),
]


# Justified equivalent mutants (removed from the denominator): removing the explicit cross-domain check in DerivedOrderStatement does not change
# behaviour, because the next lines call Interval.precedes/order, which raise ClockDomainError (a ValueError => pydantic ValidationError) for
# different domains. The explicit check is kept as defence in depth and for a clear message.
EQUIVALENT = ["derived order cross-domain allowed"]
