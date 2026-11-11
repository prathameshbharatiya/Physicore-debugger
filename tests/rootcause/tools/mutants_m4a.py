"""Mutants of the M4a modules (model.py, modelcheck.py, the CLI command). Run: PYTHONPATH=src:. python -m tests.rootcause.tools.mutants_m4a [name fragments]

Justified equivalents (5 of 117; they survive every test by construction):
  - "mo: bad binding field still kept" and "mo: constraint kept despite problems": one problem anywhere makes the whole file invalid, so an item that is
    kept despite a problem is never returned.
  - "mo: digest not canonical": every dictionary of the normalised content is built in a fixed key order, so sorting the keys again changes nothing.
  - "mo: NUL accepted": a NUL character inside a JSON or YAML text is refused by the JSON or YAML parser itself, or by the control-character check.
  - "mo: file size unchecked before read": the read is capped at the limit plus one byte, and the parser then refuses it with the same message; the early
    check only avoids opening a file that is already known to be too large."""
from tests.localization.tools.mutate import Mutant

P = "physicore_collector/rootcause/"
TD = "tests/rootcause/"
MOD = (TD + "test_model.py",)
CHK = (TD + "test_modelcheck.py",)
CLM = (TD + "test_cli_model.py",)


def M(name, file, old, new, tests):
    return Mutant(name, P + file, old, new, tests)


MUTANTS = [
    # ---- model.py: top level
    M("mo: version type not checked", "model.py", 'elif not _is_int(raw["system_model_schema_version"]) or raw["system_model_schema_version"] != SCHEMA_VERSION:', 'elif raw["system_model_schema_version"] != SCHEMA_VERSION:', MOD),
    M("mo: version value not checked", "model.py", ' or raw["system_model_schema_version"] != SCHEMA_VERSION:', ":", MOD),
    M("mo: empty declared_by accepted", "model.py", '        if top is not None and not top.strip():\n            c.add("$.declared_by"', '        if False:\n            c.add("$.declared_by"', MOD),
    M("mo: system_name optional", "model.py", '    if "system_name" not in raw:\n        c.add("$.system_name", "required")', '    if False:\n        c.add("$.system_name", "required")', MOD),
    M("mo: empty system_name accepted", "model.py", 'elif name is not None and not name.strip():', "elif False:", MOD),
    M("mo: dependency digest unchecked", "model.py", 'if not isinstance(dep_sha, str) or not _SHA_RE.fullmatch(dep_sha):', "if False:", MOD),
    M("mo: unknown top keys accepted", "model.py", '    _check_keys(raw, TOP_KEYS, "$", c)', "    pass", MOD),
    # ---- components
    M("mo: component role unchecked", "model.py", 'elif not isinstance(role, str) or role not in ROLES:', "elif False:", MOD),
    M("mo: duplicate component accepted", "model.py", "            if cid in comp_ids:", "            if False:", MOD),
    M("mo: component keys unchecked", "model.py", '_check_keys(d, COMPONENT_KEYS, p, c)', "pass", MOD),
    M("mo: component id format unchecked", "model.py", "    if s is not None and not _ID_RE.fullmatch(s):", "    if False:", MOD),
    # ---- bindings
    M("mo: binding component unchecked", "model.py", '        if "component" in d and (not isinstance(comp, str) or comp not in comp_ids):\n            c.add(f"{p}.component", "must name a declared component")\n            comp = None\n        role = _id', '        if False:\n            comp = None\n        role = _id', MOD),
    M("mo: duplicate binding accepted", "model.py", "            if (comp, role) in bound:", "            if False:", MOD),
    M("mo: topic format unchecked", "model.py", "        if topic is not None and not _TOPIC_RE.fullmatch(topic):", "        if False:", MOD),
    M("mo: field format unchecked", "model.py", "            if fld is not None and not _FIELD_RE.fullmatch(fld):", "            if False:", MOD),
    M("mo: type format unchecked", "model.py", "            if typ is not None and not _TYPE_RE.fullmatch(typ):", "            if False:", MOD),
    M("mo: bad binding field still kept", "model.py", ' and not (("field" in d and fld is None) or ("type" in d and typ is None)):', ":", MOD),
    M("mo: binding keys unchecked", "model.py", "_check_keys(d, BINDING_KEYS, p, c)", "pass", MOD),
    # ---- versions
    M("mo: version kind unchecked", "model.py", 'if "kind" in d and (not isinstance(kind, str) or kind not in VERSION_KINDS):', "if False:", MOD),
    M("mo: duplicate version accepted", "model.py", "            if (comp, kind, nm) in seen_v:", "            if False:", MOD),
    M("mo: version digest unchecked", "model.py", '            if not isinstance(dg, str) or not _SHA_RE.fullmatch(dg):', "            if False:", MOD),
    M("mo: empty name or version accepted", "model.py", '            if v is not None and not v.strip():', "            if False:", MOD),
    M("mo: version component unchecked", "model.py", '        comp = d.get("component")\n        if "component" in d and (not isinstance(comp, str) or comp not in comp_ids):\n            c.add(f"{p}.component", "must name a declared component")\n            comp = None\n        kind = d.get("kind")\n        if "kind" in d and (not isinstance(kind, str) or kind not in VERSION_KINDS):', '        comp = d.get("component")\n        kind = d.get("kind")\n        if "kind" in d and (not isinstance(kind, str) or kind not in VERSION_KINDS):', MOD),
    # ---- constraints
    M("mo: constraint kind unchecked", "model.py", "        elif not isinstance(kind, str) or kind not in CONSTRAINT_KINDS:", "        elif False:", MOD),
    M("mo: duplicate constraint accepted", "model.py", "            if cid in seen_c:", "            if False:", MOD),
    M("mo: constraint reference unresolved", "model.py", '        if (comp, role) not in bound:', "        if False:", MOD),
    M("mo: min greater than max accepted", "model.py", "            if lo is not None and hi is not None and lo > hi:", "            if False:", MOD),
    M("mo: negative rate accepted", "model.py", '                    if v is not None and v < 0:', "                    if False:", MOD),
    M("mo: empty range accepted", "model.py", "            if lo_k not in d and hi_k not in d:", "            if False:", MOD),
    M("mo: exact type check relaxed", "model.py", "if type(v) not in (int, float) or", "if not isinstance(v, (int, float)) or", MOD),
    M("mo: non-finite number accepted", "model.py", "or (isinstance(v, float) and not math.isfinite(v)):   # type() is exact, so a boolean is refused here", ":", MOD),
    M("mo: deadline not positive", "model.py", '            elif not _is_int(d["deadline_ns"]) or d["deadline_ns"] <= 0:', "            elif False:", MOD),
    M("mo: members count unchecked", "model.py", '            elif not isinstance(mem, list) or len(mem) < 2:', "            elif not isinstance(mem, list):", MOD),
    M("mo: duplicate member accepted", "model.py", "                    elif m in members:", "                    elif False:", MOD),
    M("mo: min_healthy upper bound", "model.py", " or (isinstance(mem, list) and mh > len(mem))", "", MOD),
    M("mo: min_healthy lower bound", "model.py", "elif not _is_int(mh) or mh < 1 or", "elif not _is_int(mh) or", MOD),
    M("mo: constraint kept despite problems", "model.py", "        if c.total == n_before and cid is not None and by is not None:\n            constraints.append(out)", "        if True:\n            constraints.append(out)", MOD),
    # ---- interventions
    M("mo: intervention kind unchecked", "model.py", 'if "kind" in d and (not isinstance(kind, str) or kind not in INTERVENTION_KINDS):', "if False:", MOD),
    M("mo: negative time accepted", "model.py", 'if "at_ns" in d and (not _is_int(at) or at < 0):', 'if "at_ns" in d and (not _is_int(at)):', MOD),
    M("mo: clock domain unchecked", "model.py", 'if "clock_domain" in d and (not isinstance(cd, str) or cd not in CLOCK_DOMAINS):', "if False:", MOD),
    M("mo: restart with parameter accepted", "model.py", '                if kind == "restart":', "                if False:", MOD),
    M("mo: parameter not required", "model.py", '        if kind == "parameter_change" and "parameter" not in d:', "        if False:", MOD),
    M("mo: duplicate intervention accepted", "model.py", "            if iid in seen_i:", "            if False:", MOD),
    # ---- edges
    M("mo: edge relation unchecked", "model.py", 'if "relation" in e and (not isinstance(rel, str) or rel not in EDGE_RELATIONS):', "if False:", MOD),
    M("mo: self edge accepted", "model.py", "        if ends[0] is not None and ends[0] == ends[1]:", "        if False:", MOD),
    M("mo: edge endpoint unchecked", "model.py", "                elif val not in comp_ids:", "                elif False:", MOD),
    M("mo: duplicate edge accepted", "model.py", "            if eid in seen_e:", "            if False:", MOD),
    M("mo: window not positive", "model.py", '            if not _is_int(w) or w <= 0:', "            if not _is_int(w):", MOD),
    M("mo: edge topic format unchecked", "model.py", "                    if not _TOPIC_RE.fullmatch(val):", "                    if False:", MOD),
    # ---- declared_by, text, limits, digest, files
    M("mo: item declaration ignored", "model.py", '    if "declared_by" in d:', "    if False:", MOD),
    M("mo: wrong declaration source", "model.py", '    return top, "file"', '    return top, "item"', MOD),
    M("mo: control chars allowed", "model.py", "    if _bad(v, newline_ok=newline_ok):", "    if False:", MOD),
    M("mo: newline allowed everywhere", "model.py", "unicodedata.category(c).startswith(\"C\") and not (newline_ok and c == \"\\n\")", "unicodedata.category(c).startswith(\"C\") and not c == \"\\n\"", MOD),
    M("mo: string length unchecked", "model.py", "    if len(v) > limit:\n        c.add(path, f\"{what} is longer than {limit} characters\")", "    if False:\n        c.add(path, f\"{what} is longer than {limit} characters\")", MOD),
    M("mo: section limit unchecked", "model.py", "    if len(v) > limit:\n        c.add(f\"$.{key}\"", "    if False:\n        c.add(f\"$.{key}\"", MOD),
    M("mo: file limit off by one", "model.py", "if len(data) > lim[\"file_bytes\"]:", "if len(data) > lim[\"file_bytes\"] + 1:", MOD),
    M("mo: digest not canonical", "model.py", 'json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)', 'json.dumps(content, separators=(",", ":"), ensure_ascii=False)', MOD),
    M("mo: lists not ordered", "model.py", '"components": sorted(components, key=lambda x: x["id"])', '"components": components', MOD),
    M("mo: edges not ordered", "model.py", '"edges": sorted(edges, key=lambda x: x["id"])}', '"edges": edges}', MOD),
    M("mo: interventions not ordered", "model.py", '"interventions": sorted(interventions, key=lambda x: (x["at_ns"], x["id"]))', '"interventions": interventions', MOD),
    M("mo: bindings not ordered", "model.py", '"bindings": sorted(bindings, key=lambda x: (x["component"], x["role"]))', '"bindings": bindings', MOD),
    M("mo: NUL accepted", "model.py", '        if "\\x00" in text:', "        if False:", MOD),
    M("mo: lenient decoding", "model.py", '            text = data.decode("utf-8")', '            text = data.decode("utf-8", errors="replace")', MOD),
    M("mo: symlink followed", "model.py", "    if stat.S_ISLNK(st.st_mode):", "    if False:", MOD),
    M("mo: non-regular file read", "model.py", "    if not stat.S_ISREG(st.st_mode):", "    if False:", MOD),
    M("mo: file size unchecked before read", "model.py", "    if st.st_size > limit:", "    if False:", MOD),
    M("mo: json duplicate keys accepted", "model.py", "object_pairs_hook=_json_pairs, ", "", MOD),
    M("mo: json constants accepted", "model.py", ", parse_constant=_no_const", "", MOD),
    M("mo: json scan skipped", "model.py", "            msg = _scan_json(text, MAX_NESTING)\n            if msg:", "            msg = None\n            if msg:", MOD),
    M("mo: yaml scan skipped", "model.py", "            msg = _scan_yaml(text, MAX_NESTING)\n            if msg:", "            msg = None\n            if msg:", MOD),
    M("mo: empty file accepted", "model.py", "        if raw is None:", "        if False:", MOD),
    M("mo: json extension ignored", "model.py", 'if name.lower().endswith(".json"):', "if False:", MOD),
    # ---- modelcheck.py
    M("ck: undeclared topic ignored", "modelcheck.py", "    for t in sorted(used - set(rec)):", "    for t in []:", CHK),
    M("ck: unrecorded topic ignored", "modelcheck.py", "    for t in sorted(set(rec) - used):", "    for t in []:", CHK),
    M("ck: type mismatch ignored", "modelcheck.py", 'if r is not None and b["type"] is not None and r["types"] and b["type"] not in r["types"]:', "if False:", CHK),
    M("ck: type mismatch without recorded type", "modelcheck.py", ' and r["types"] and b["type"] not in r["types"]:', ' and b["type"] not in r["types"]:', CHK),
    M("ck: type mismatch needs one type only", "modelcheck.py", 'b["type"] not in r["types"]:', 'b["type"] != r["types"][0]:', CHK),
    M("ck: field requirement dropped", "modelcheck.py", '        if need_field and b["field"] is None:', "        if False:", CHK),
    M("ck: topic requirement dropped", "modelcheck.py", '        if b["topic"] not in rec:\n            rs.append("topic_not_recorded")', '        if False:\n            rs.append("topic_not_recorded")', CHK),
    M("ck: rate needs a field", "modelcheck.py", "            rs = reasons(k, need_field=False)", "            rs = reasons(k, need_field=True)", CHK),
    M("ck: value range needs no field", "modelcheck.py", '            rs = reasons(k, need_field=True)\n        elif k["kind"] == "rate"', '            rs = reasons(k, need_field=False)\n        elif k["kind"] == "rate"', CHK),
    M("ck: deadline reasons duplicated", "modelcheck.py", 'rs = sorted(set(reasons(k["trigger"], need_field=True) + reasons(k["response"], need_field=True)))', 'rs = sorted(reasons(k["trigger"], need_field=True) + reasons(k["response"], need_field=True))', CHK),
    M("ck: deadline response ignored", "modelcheck.py", '+ reasons(k["response"], need_field=True)', "", CHK),
    M("ck: redundancy binding check dropped", "modelcheck.py", '            if any(not any(b["component"] == m for b in c["bindings"]) for m in k["members"]):', "            if False:", CHK),
    M("ck: redundancy needs all members recorded", "modelcheck.py", '            elif all(not any(b["topic"] in rec for b in c["bindings"] if b["component"] == m) for m in k["members"]):', '            elif any(not any(b["topic"] in rec for b in c["bindings"] if b["component"] == m) for m in k["members"]):', CHK),
    M("ck: window lower bound exclusive", "modelcheck.py", 'if w is not None and not (w["min_log_time_ns"] <= i["at_ns"] <= w["max_log_time_ns"]):', 'if w is not None and not (w["min_log_time_ns"] < i["at_ns"] <= w["max_log_time_ns"]):', CHK),
    M("ck: window upper bound exclusive", "modelcheck.py", 'if w is not None and not (w["min_log_time_ns"] <= i["at_ns"] <= w["max_log_time_ns"]):', 'if w is not None and not (w["min_log_time_ns"] <= i["at_ns"] < w["max_log_time_ns"]):', CHK),
    M("ck: no window means outside", "modelcheck.py", 'if w is not None and not (w["min_log_time_ns"]', 'if w is None or not (w["min_log_time_ns"]', CHK),
    M("ck: digest mismatch ignored", "modelcheck.py", '    return [] if dependency_sha == want else [_finding("dependency_file_digest_mismatch", want, supplied=dependency_sha)]', "    return []", CHK),
    M("ck: missing file ignored", "modelcheck.py", '    if not supplied:\n        return [_finding("dependency_file_not_supplied", want)]', "    if not supplied:\n        return []", CHK),
    M("ck: severity of notes", "modelcheck.py", 'SEVERITY = {"recorded_topic_not_declared": "note", "dependency_file_not_supplied": "note"}', 'SEVERITY = {"recorded_topic_not_declared": "note"}', CHK),
    M("ck: findings unordered", "modelcheck.py", '    out.sort(key=lambda f: (KIND_ORDER.index(f["kind"]), f["subject"], json.dumps(f["detail"], sort_keys=True)))', "", CHK),
    M("ck: order ignores subject", "modelcheck.py", '(KIND_ORDER.index(f["kind"]), f["subject"], json.dumps', '(KIND_ORDER.index(f["kind"]), json.dumps', CHK),
    M("ck: channels not merged", "modelcheck.py", '        e["message_count"] += t.message_count', "        e[\"message_count\"] = t.message_count", CHK),
    M("ck: types not collected", "modelcheck.py", '        if t.schema_name:\n            e["types"].add(t.schema_name)', "        pass", CHK),
    M("ck: window dropped", "modelcheck.py", 'None if w is None else {"min_log_time_ns"', 'None if True else {"min_log_time_ns"', CHK),
    M("ck: finding cap", "modelcheck.py", "MAX_FINDINGS = 200", "MAX_FINDINGS = 201", CHK),
    M("ck: truncation flag", "modelcheck.py", '"findings_truncated": bool(findings is not None and len(findings) > MAX_FINDINGS),', '"findings_truncated": False,', CHK),
    M("ck: total not kept", "modelcheck.py", "    total = None if findings is None else len(findings)", "    total = None if findings is None else min(len(findings), MAX_FINDINGS)", CHK),
    M("ck: recording marker lost", "modelcheck.py", '"recording": facts if facts is not None else {"source": "not_supplied"},', '"recording": facts if facts is not None else {},', CHK),
    M("ck: declared marker lost", "modelcheck.py", '"model": {"source": "declared",', '"model": {"source": "recording",', CHK),
    M("ck: json not sorted", "modelcheck.py", "return json.dumps(rep, indent=2, sort_keys=True, ensure_ascii=False)", "return json.dumps(rep, indent=2, ensure_ascii=False)", CHK),
    M("ck: limits dropped from text", "modelcheck.py", '    L.append("Limits:")', '    L.append("")', CHK),
    M("ck: text hides context", "modelcheck.py", '    if c["operating_context"]:', "    if False:", CHK),
    M("ck: topics not listed in text", "modelcheck.py", "        for t in rec[\"topics\"]:\n            L.append", "        for t in []:\n            L.append", CHK + CLM),
    M("ck: exit code from inventory ignored", "modelcheck.py", '    facts = recording_facts(inv)\n    findings = check_model(model, facts, dependency_sha=dep_sha, dependency_supplied=supplied)\n    return {"exit_code": code,', '    facts = recording_facts(inv)\n    findings = check_model(model, facts, dependency_sha=dep_sha, dependency_supplied=supplied)\n    return {"exit_code": 0,', CHK + CLM),
    M("ck: failed inspection not fatal", "modelcheck.py", "    if code == 2:", "    if False:", CHK + CLM),
    M("ck: invalid model exit code", "modelcheck.py", 'return {"exit_code": 3, "problems": lines, "report": None}', 'return {"exit_code": 2, "problems": lines, "report": None}', CHK + CLM),
    M("ck: problems not truncated note", "modelcheck.py", "        if loaded.problems_total > len(loaded.problems):", "        if False:", CHK + CLM),
    M("ck: dependency read error exit", "modelcheck.py", 'return {"exit_code": 2, "problems": [f"dependency file: {why}"], "report": None}', 'return {"exit_code": 3, "problems": [f"dependency file: {why}"], "report": None}', CHK + CLM),
    M("ck: dependency digest of wrong bytes", "modelcheck.py", "dep_sha, supplied = hashlib.sha256(data).hexdigest(), True", 'dep_sha, supplied = hashlib.sha256(data[:-1]).hexdigest(), True', CHK + CLM),
    # ---- cli
    M("cli: stdin accepted", "../cli.py", '    if "-" in (str(model), str(evidence), str(dependencies)):', "    if False:", CLM),
    M("cli: missing evidence accepted", "../cli.py", '    if evidence is not None and not evidence.exists():', "    if False:", CLM),
    M("cli: json flag ignored", "../cli.py", "typer.echo(to_json(result[\"report\"]) if json_output else render_text(result[\"report\"]), nl=False)", "typer.echo(render_text(result[\"report\"]), nl=False)", CLM),
    M("cli: exit code dropped", "../cli.py", '    if result["exit_code"]:\n        raise typer.Exit(code=result["exit_code"])', "    pass", CLM),
    M("cli: problems not printed", "../cli.py", "        for line in result[\"problems\"]:\n            typer.echo(line, err=True)\n        raise typer.Exit(code=result[\"exit_code\"])", "        raise typer.Exit(code=result[\"exit_code\"])", CLM),
    M("cli: problems on stdout", "../cli.py", "            typer.echo(line, err=True)\n        raise typer.Exit(code=result[\"exit_code\"])", "            typer.echo(line)\n        raise typer.Exit(code=result[\"exit_code\"])", CLM),
]


def main() -> int:
    import sys
    from tests.localization.tools.mutate import run
    only = set(sys.argv[1:])
    ms = [m for m in MUTANTS if not only or any(o in m.name for o in only)]
    res = run(ms)
    w = max(len(n) for n, _ in res)
    for n, r in res:
        print(f"{n.ljust(w)}  {r}", flush=True)
    bad = [r for r in res if r[1] not in ("KILLED", "PASS")]
    print(f"\n{sum(1 for _, r in res if r == 'KILLED')} killed, {len(bad)} not killed or invalid of {len(ms)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
