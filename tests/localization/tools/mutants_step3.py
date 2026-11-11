from tests.localization.tools.mutate import Mutant

P = "physicore_collector/localization/dependencies.py"
T = "tests/localization/"
DEP = (T + "test_dep.py",)

MUTANTS = [
    # ---- loader / validation
    Mutant("unknown top key accepted", P, '    _check_keys(raw, TOP_KEYS, "$", c)', "    pass", DEP),
    Mutant("unknown edge key accepted", P, "        _check_keys(e, EDGE_KEYS, p, c)", "        pass", DEP),
    Mutant("unknown node key accepted", P, "        _check_keys(n, NODE_KEYS, p, c)", "        pass", DEP),
    Mutant("unknown binding key accepted", P, "        _check_keys(b, BINDING_KEYS, p, c)", "        pass", DEP),
    Mutant("unknown identity key accepted", P, '                _check_keys(ident, IDENTITY_KEYS, f"{p}.identity", c)', "                pass", DEP),
    Mutant("schema version not enforced", P, "    elif not _is_int(v) or v != SCHEMA_VERSION:", "    elif False:", DEP),
    Mutant("declared_by may be blank", P, "        if declared_by is not None and not declared_by.strip():", "        if False:", DEP),
    Mutant("declared_by not required", P, '    if "declared_by" not in raw:\n        c.add("$.declared_by", "required")\n    else:', "    if False:\n        pass\n    else:", DEP),
    Mutant("duplicate edge id allowed", P, "            elif eid in seen_edges:", "            elif False:", DEP),
    Mutant("self-loop allowed", P, "        if ends[0] is not None and ends[0] == ends[1]:", "        if False:", DEP),
    Mutant("undeclared node endpoint allowed", P, "                elif val not in seen_nodes:", "                elif False:", DEP),
    Mutant("relation vocabulary open", P, "        if \"relation\" in e and (not isinstance(rel, str) or rel not in EDGE_RELATIONS):", "        if False:", DEP),
    Mutant("window zero allowed", P, "            if not _is_int(w) or w <= 0:", "            if not _is_int(w) or w < 0:", DEP),
    Mutant("window bool/float accepted", P, "def _is_int(v: Any) -> bool:\n    return type(v) is int", "def _is_int(v: Any) -> bool:\n    return isinstance(v, (int, float))", DEP),
    Mutant("empty edge list allowed", P, '    elif not isinstance(edges_raw, list) or not edges_raw:', '    elif not isinstance(edges_raw, list):', DEP),
    Mutant("edge count limit off by one", P, '    if len(edges_raw) > limits["dependency_edges"]:', '    if len(edges_raw) > limits["dependency_edges"] + 1:', DEP),
    Mutant("node count limit off by one", P, '    if len(nodes_raw) > limits["dependency_nodes"]:', '    if len(nodes_raw) > limits["dependency_nodes"] + 1:', DEP),
    Mutant("string limit off by one", P, "    if len(v) > limit:", "    if len(v) > limit + 1:", DEP),
    Mutant("control characters allowed", P, '    return any(unicodedata.category(c).startswith("C") for c in s)', "    return False", DEP),
    Mutant("only control (Cc) characters refused", P, 'unicodedata.category(c).startswith("C")', 'unicodedata.category(c) == "Cc"', DEP),
    Mutant("node id may start with slash", P, '_ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]*")', '_ID_RE = re.compile(r"[A-Za-z0-9_/][A-Za-z0-9_./:-]*")', DEP),
    Mutant("topic charset open", P, '_TOPIC_RE = re.compile(r"/[A-Za-z0-9_/.~-]*")', '_TOPIC_RE = re.compile(r"/.*")', DEP),
    Mutant("duplicate node id allowed", P, "        if nid in seen_nodes:", "        if False:", DEP),
    Mutant("binding to unknown node allowed", P, '        if "node" in b and (not isinstance(node, str) or node not in seen_nodes):', "        if False:", DEP),
    Mutant("duplicate binding allowed", P, "            if key in seen_b:", "            if False:", DEP),
    Mutant("empty identity value allowed", P, '                        if val is not None and val == "":', "                        if False:", DEP),
    Mutant("size limit not enforced", P, '        if len(data) > lim["dependency_file_bytes"]:', "        if False:", DEP),
    Mutant("size limit off by one", P, '        if len(data) > lim["dependency_file_bytes"]:', '        if len(data) >= lim["dependency_file_bytes"]:', DEP),
    Mutant("NUL allowed", P, '        if "\\x00" in text:', "        if False:", DEP),
    Mutant("yaml anchors allowed", P, '            if getattr(ev, "anchor", None) is not None:', "            if False:", DEP),
    Mutant("yaml tags allowed", P, '            if getattr(ev, "tag", None) is not None:', "            if False:", DEP),
    Mutant("yaml merge keys allowed", P, '                    if ev.value == "<<":', "                    if False:", DEP),
    Mutant("yaml duplicate keys allowed", P, "                    if ev.value in top[\"keys\"]:", "                    if False:", DEP),
    Mutant("yaml nesting unlimited", P, "                if len(stack) > limit:", "                if False:", DEP),
    Mutant("yaml nesting limit off by a lot", P, "MAX_NESTING = 8 ", "MAX_NESTING = 800 ", DEP),
    Mutant("json nesting unchecked", P, "            msg = _scan_json(text, MAX_NESTING)\n            if msg:", "            msg = None\n            if msg:", DEP),
    Mutant("json brackets in strings counted", P, '        if in_str:\n            if esc:', '        if False:\n            if esc:', DEP),
    Mutant("json duplicate keys allowed", P, "        if k in d:\n            raise ValueError", "        if False:\n            raise ValueError", DEP),
    Mutant("json NaN allowed", P, "parse_constant=_no_const", "parse_constant=None", DEP),
    Mutant("symlink followed", P, "    if stat.S_ISLNK(st.st_mode):", "    if False:", DEP),
    Mutant("non-regular file opened", P, "    if not stat.S_ISREG(st.st_mode):", "    if False:", DEP),
    Mutant("empty file accepted as valid", P, "        if raw is None:", "        if False:", DEP),
    Mutant("problems unbounded", P, "        if len(self.items) < MAX_PROBLEMS:", "        if True:", DEP),
    Mutant("problem total not counted", P, "        self.total += 1\n", "        self.total += 0\n", DEP),
    # ---- hash
    Mutant("semantic hash includes edge order", P, '"edges": sorted(edges, key=lambda e: e["id"])}', '"edges": edges}', DEP),
    Mutant("semantic hash ignores declared_by", P, '"declared_by": content["declared_by"], "nodes"', '"nodes"', DEP),
    Mutant("semantic hash ignores windows", P, '"edges": content["edges"]}', '"edges": [{k: v for k, v in e.items() if k != "window_ns"} for e in content["edges"]]}', DEP),
    Mutant("semantic hash ignores relation", P, '"edges": content["edges"]}', '"edges": [{k: v for k, v in e.items() if k != "relation"} for e in content["edges"]]}', DEP),
    Mutant("semantic hash ignores direction", P, '"edges": content["edges"]}', '"edges": [{**e, "from": min(e["from"], e["to"]), "to": max(e["from"], e["to"])} for e in content["edges"]]}', DEP),
    Mutant("semantic hash ignores bindings", P, '"bindings": sorted(', '"bindings": [] and sorted(', DEP),
    Mutant("file digest of text not bytes", P, "    sha = hashlib.sha256(data).hexdigest()", "    sha = hashlib.sha256(data.strip()).hexdigest()", DEP),
    # ---- graph / paths
    Mutant("node_for fuzzy match", P, "identities.get(kind) == val", "(identities.get(kind) or '').strip().lower() == val.strip().lower()", DEP),
    Mutant("node_for ignores topic", P, "            if b.topic != topic:\n                continue", "            pass", DEP),
    Mutant("node_for any-key instead of all", P, "        if all(any(identities.get(kind) == val", "        if any(any(identities.get(kind) == val", DEP),
    Mutant("ambiguous picks first", P, "        if len(hits) > 1:\n            return topic, \"ambiguous\"", "        if False:\n            return topic, \"ambiguous\"", DEP),
    Mutant("name kind limited to logger", P, 'NAME_KINDS = ("logger_name", "diagnostic_name")', 'NAME_KINDS = ("logger_name",)', DEP),
    Mutant("ancestors misses indirect", P, "                    stack.append(e.src)\n        return sorted(seen)", "                    pass\n        return sorted(seen)", DEP),
    Mutant("paths: depth bound ignored", P, "        if len(trail) + 1 > max_depth:", "        if False:", DEP),
    Mutant("paths: depth bound off by one", P, "        if len(trail) + 1 > max_depth:", "        if len(trail) + 1 > max_depth + 1:", DEP),
    Mutant("paths: pair cap ignored", P, "            if len(paths) >= max_paths:", "            if False:", DEP),
    Mutant("paths: pair cap off by one", P, "            if len(paths) >= max_paths:", "            if len(paths) > max_paths:", DEP),
    Mutant("paths: budget ignored", P, "        if not budget.take():", "        if False:", DEP),
    Mutant("paths: cycles revisit nodes", P, "        if e.dst not in useful or e.dst in on_path:", "        if e.dst not in useful:", DEP),
    Mutant("paths: no reachability pruning", P, "        if e.dst not in useful or e.dst in on_path:", "        if e.dst in on_path:", DEP),
    Mutant("paths: unsorted edge order", P, "        lst.sort(key=lambda e: (e.dst, e.id))", "        lst.sort(key=lambda e: e.id[::-1])", DEP),
    Mutant("paths: depth cut not flagged", P, "            depth_cut = True\n            continue", "            continue", DEP),
    Mutant("paths: complete claimed after cap", P, "    return PathResult(paths, not (pair_cap or work_out), depth_cut, pair_cap, work_out)", "    return PathResult(paths, True, depth_cut, pair_cap, work_out)", DEP),
    Mutant("paths: exact total despite depth cut", P, "        return len(self.paths) if self.complete and not self.depth_truncated else None", "        return len(self.paths) if self.complete else None", DEP),
    Mutant("budget: never exhausts", P, "        return self.used >= self.limit", "        return False", DEP),
    Mutant("budget: take ignores the limit", P, "        if self.used >= self.limit:\n            return False\n        self.used += 1", "        self.used += 1", DEP),
]


# Justified equivalent mutants (removed from the denominator, each exercised through its redundant defence elsewhere):
#  - "yaml aliases allowed": an alias cannot exist without an anchor, and anchors are refused (PyYAML also errors on an undefined alias).
#  - "yaml complex keys allowed": PyYAML's safe constructor itself rejects unhashable (sequence/mapping) keys.
#  - "yaml multi-document allowed": yaml.safe_load raises on more than one document.
#  - "file size not checked before reading": the read is capped at limit+1 bytes and parse_dependencies refuses the oversize bytes with the same problem.
#  - "paths: src==dst yields a path": src is always on the current path, so no path to itself can be emitted.
EQUIVALENT = ["yaml aliases allowed", "yaml complex keys allowed", "yaml multi-document allowed", "file size not checked before reading", "paths: src==dst yields a path"]
