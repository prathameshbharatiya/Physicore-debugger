"""Mutants of the M4b modules (catalogue.py, instantiate.py, review_pack.py). Run: PYTHONPATH=src:. python -m tests.rootcause.tools.mutants_m4b [name fragments]

Justified equivalents (2 of 86; they survive every test by construction):
  - "ca: NUL accepted": a NUL character is refused by the YAML or JSON parser, or by the control-character check, with or without the early check.
  - "ca: empty file accepted as ok": an empty file gives no mapping, and the top-level check refuses it with another message."""
from tests.localization.tools.mutate import Mutant

P = "physicore_collector/rootcause/"
TD = "tests/rootcause/"
CAT = (TD + "test_catalogue.py", TD + "test_catalogue_more.py")
INS = (TD + "test_instantiate.py", TD + "test_catalogue_more.py")
PACK = (TD + "test_review_pack.py", TD + "test_catalogue_more.py")


def M(name, file, old, new, tests):
    return Mutant(name, P + file, old, new, tests)


MUTANTS = [
    # ---- catalogue.py
    M("ca: version value unchecked", "catalogue.py", 'or raw["catalogue_schema_version"] != SCHEMA_VERSION:', ":", CAT),
    M("ca: sentence full stop not required", "catalogue.py", 'if not v.endswith("."):', "if False:", CAT),
    M("ca: forbidden wording not screened", "catalogue.py", "        hits = find_forbidden(v)\n        if hits:", "        hits = []\n        if hits:", CAT),
    M("ca: empty text accepted", "catalogue.py", "    if not v.strip():\n        c.add(path, f\"{what} must not be empty\")", "    if False:\n        c.add(path, f\"{what} must not be empty\")", CAT),
    M("ca: long text accepted", "catalogue.py", "    if len(v) > lim:", "    if False:", CAT),
    M("ca: control chars accepted", "catalogue.py", "    if _bad_chars(v):\n        c.add(path, f\"{what} contains", "    if False:\n        c.add(path, f\"{what} contains", CAT),
    M("ca: list length limit off", "catalogue.py", "    if len(v) > LIMITS[\"items_per_list\"]:", "    if False:", CAT),
    M("ca: duplicate enum value accepted", "catalogue.py", "        elif x in out:", "        elif False:", CAT),
    M("ca: builtin id not restricted", "catalogue.py", "_ID_RE.fullmatch(eid) and eid in MECHANISM_IDS_V1 if supplied_by", "_ID_RE.fullmatch(eid) if supplied_by", CAT),
    M("ca: user id prefix not required", "catalogue.py", "else _USER_ID_RE.fullmatch(eid))", "else _ID_RE.fullmatch(eid))", CAT),
    M("ca: version minimum off", "catalogue.py", 'not _is_int(ver) or ver < 1', 'not _is_int(ver)', CAT),
    M("ca: version bool accepted", "catalogue.py", 'not _is_int(ver) or ver < 1', 'not isinstance(ver, int) or ver < 1', CAT),
    M("ca: family unchecked", "catalogue.py", 'not isinstance(fam, str) or fam not in FAMILIES', "False", CAT),
    M("ca: categories optional", "catalogue.py", '"symptom_categories", f"{p}.applies_to", c, minimum=1)', '"symptom_categories", f"{p}.applies_to", c, minimum=0)', CAT),
    M("ca: roles unchecked", "catalogue.py", 'ROLES, f"{p}.applies_to.component_roles"', 'tuple(ROLES) + ("wizard",), f"{p}.applies_to.component_roles"', CAT),
    M("ca: duplicate slot accepted", "catalogue.py", "        elif nm in slot_names:", "        elif False:", CAT),
    M("ca: slot selector unchecked", "catalogue.py", 'not (sel in SLOT_SELECTS or ROLE_SELECT_RE.fullmatch(sel))', "False", CAT),
    M("ca: slots optional", "catalogue.py", 'for j, s in enumerate(_list(d, "slots", p, c, minimum=1)):', 'for j, s in enumerate(_list(d, "slots", p, c, minimum=0)):', CAT),
    M("ca: check kind unchecked", "catalogue.py", 'or k["kind"] not in CHECK_KINDS', "or False", CAT),
    M("ca: check slot unchecked", "catalogue.py", 'or k["slot"] not in slot_names', "or False", CAT),
    M("ca: constraint kind unchecked", "catalogue.py", 'not isinstance(ck, str) or ck not in CONSTRAINT_KINDS', "False", CAT),
    M("ca: duplicate check accepted", "catalogue.py", "            elif kid in check_ids:", "            elif False:", CAT),
    M("ca: duplicate prediction accepted", "catalogue.py", "            elif qid in pred_ids:", "            elif False:", CAT),
    M("ca: predictions optional", "catalogue.py", 'enumerate(_list(d, "predictions", p, c, minimum=1))', 'enumerate(_list(d, "predictions", p, c, minimum=0))', CAT),
    M("ca: rule op unchecked", "catalogue.py", 'or r["op"] not in RULE_OPS', "or False", CAT),
    M("ca: rule slot unchecked", "catalogue.py", 'or r["slot"] not in slot_names', "or False", CAT),
    M("ca: rule check unchecked", "catalogue.py", 'or r["check"] not in check_ids', "or False", CAT),
    M("ca: rule keys unchecked", "catalogue.py", "_check_keys(r, RULE_KEYS[r[\"op\"]]", "_check_keys(r, tuple(r)", CAT),
    M("ca: rule categories optional", "catalogue.py", 'f"{qp}.rule", c, minimum=1)', 'f"{qp}.rule", c, minimum=0)', CAT),
    M("ca: availability unchecked", "catalogue.py", 'or r["usual_availability"] not in AVAILABILITY', "or False", CAT),
    M("ca: duplicate token accepted", "catalogue.py", "            elif tk in tokens:", "            elif False:", CAT),
    M("ca: self rival accepted", "catalogue.py", "        elif rv == eid:", "        elif False:", CAT),
    M("ca: duplicate rival accepted", "catalogue.py", "        elif rv in seen_r:", "        elif False:", CAT),
    M("ca: user review accepted", "catalogue.py", '        if "review" in d:\n            c.add(f"{p}.review", "a user entry cannot', '        if False:\n            c.add(f"{p}.review", "a user entry cannot', CAT),
    M("ca: user status not forced", "catalogue.py", 'review = {"status": "user_supplied"', 'review = {"status": "reviewed"', CAT),
    M("ca: reviewer not required", "catalogue.py", 'if not isinstance(by, str) or not by.strip() or len(by) > LIMITS["string_chars"] or _bad_chars(by):', "if False:", CAT),
    M("ca: review date not required", "catalogue.py", 'if not isinstance(on, str) or not _DATE_RE.fullmatch(on):', "if False:", CAT),
    M("ca: proposed with reviewer accepted", "catalogue.py", 'elif st == "proposed" and ("reviewed_by" in rv or "reviewed_on" in rv):', "elif False:", CAT),
    M("ca: builtin review optional", "catalogue.py", '    elif "review" not in d:\n        c.add(f"{p}.review", "required")', '    elif False:\n        c.add(f"{p}.review", "required")', CAT),
    M("ca: duplicate entry accepted", "catalogue.py", "                if e[\"id\"] in seen:", "                if False:", CAT),
    M("ca: empty entries accepted", "catalogue.py", "    elif not items:", "    elif False:", CAT),
    M("ca: entries limit off", "catalogue.py", 'elif len(items) > LIMITS["entries"]:', "elif False:", CAT),
    M("ca: builtin completeness unchecked", "catalogue.py", 'sorted(seen) != sorted(MECHANISM_IDS_V1)', "False", CAT),
    M("ca: file size unchecked", "catalogue.py", 'if len(data) > LIMITS["file_bytes"]:', "if False:", CAT),
    M("ca: NUL accepted", "catalogue.py", 'if "\\x00" in text:', "if False:", CAT),
    M("ca: empty file accepted as ok", "catalogue.py", "        if raw is None:", "        if False:", CAT),
    M("ca: json dup keys not caught", "catalogue.py", "object_pairs_hook=_json_pairs, ", "", CAT),
    M("ca: digest not sorted", "catalogue.py", "json.dumps(list(entries), sort_keys=True,", "json.dumps(list(entries), sort_keys=False,", CAT),
    M("ca: entries not sorted", "catalogue.py", "    return sorted(entries, key=lambda e: e[\"id\"]), by", "    return entries, by", CAT),
    M("ca: rival existence unchecked (user)", "catalogue.py", "    _check_rivals(user.entries, allentries)", "    pass", CAT),
    M("ca: merged drops user", "catalogue.py", "list(b.entries) + list(user.entries)", "list(b.entries)", CAT),
    M("ca: failed user load merged silently", "catalogue.py", "    if user.entries is None:\n        raise", "    if False:\n        raise", CAT),
    # ---- instantiate.py
    M("in: unit categories ignored", "instantiate.py", "    if not set(entry[\"applies_to\"][\"symptom_categories\"]) & set(unit[\"categories\"]):\n        return False", "    pass", INS),
    M("in: roles ignored", "instantiate.py", "    return bool(set(want) & have)", "    return True", INS),
    M("in: roles optional match flipped", "instantiate.py", "    if not want:\n        return True", "    if not want:\n        return False", INS),
    M("in: lone symptoms dropped", "instantiate.py", "    for sid in sorted(set(sym) - in_comp):", "    for sid in []:", INS),
    M("in: components dropped", "instantiate.py", "    for c in report[\"components\"]:", "    for c in []:", INS),
    M("in: unit topics not unique", "instantiate.py", '"topics": sorted({s["node"] for s in ss})', '"topics": sorted([s["node"] for s in ss])', INS),
    M("in: upstream from wrong end", "instantiate.py", "        if t & utopics:\n            up.update", "        if f & utopics:\n            up.update", INS),
    M("in: downstream from wrong end", "instantiate.py", "        if f & utopics:\n            down.update", "        if t & utopics:\n            down.update", INS),
    M("in: model edges ignored", "instantiate.py", 'edges = [(e, "system_model") for e in medges] +', "edges = [] +", INS),
    M("in: dependency edges ignored", "instantiate.py", "+ [({\"from\": e[\"from\"], \"to\": e[\"to\"]}, \"dependency_file\") for e in dep_edges]", "", INS),
    M("in: upstream includes symptom topics", "instantiate.py", "got = set(m) - utopics", "got = set(m)", INS),
    M("in: siblings include symptom topics", "instantiate.py", "for t in comp_topics.get(c, ())} - utopics", "for t in comp_topics.get(c, ())}", INS),
    M("in: role slot ignores component", "instantiate.py", 'if b["role"] == role and b["component"] in ucomps}', 'if b["role"] == role}', INS),
    M("in: unbound source label lost", "instantiate.py", 'srcs[s["name"]] = src if got else "not_bound"', 'srcs[s["name"]] = src', INS),
    M("in: component endpoint not expanded", "instantiate.py", "return {ref} if ref.startswith(\"/\") else set(comp_topics.get(ref, ()))", "return {ref}", INS),
    M("in: check always runnable", "instantiate.py", 'ok = bool(binds.get(k["slot"]))', "ok = True", INS),
    M("in: missing state text changed", "instantiate.py", 'BINDING_MISSING = "not runnable: binding missing"', 'BINDING_MISSING = "skipped"', INS),
    M("in: instance id ignores bindings", "instantiate.py", 'entry["version"], unit["unit_id"], binds)', 'entry["version"], unit["unit_id"])', INS),
    M("in: instance id ignores version", "instantiate.py", 'entry["id"], entry["version"], unit["unit_id"], binds)', 'entry["id"], unit["unit_id"], binds)', INS),
    M("in: instance id ignores unit", "instantiate.py", 'entry["version"], unit["unit_id"], binds)', 'entry["version"], binds)', INS),
    M("in: unresolved member dropped", "instantiate.py", '+ [{"member": "unresolved", "sentence": UNRESOLVED_SENTENCE}]', "", INS),
    M("in: no-mechanism member always added", "instantiate.py", "        if not inst:\n            members.append", "        if True:\n            members.append", INS),
    M("in: no-mechanism member never added", "instantiate.py", "        if not inst:\n            members.append", "        if False:\n            members.append", INS),
    M("in: instances unsorted", "instantiate.py", '        inst.sort(key=lambda i: (i["entry_id"], i["instance_id"]))', "        pass", INS),
    M("in: covered topics omit bindings", "instantiate.py", "| {t for i in inst for ts in i[\"bindings\"].values() for t in ts})", ")", INS),
    M("in: catalogue digest dropped", "instantiate.py", '"catalogue_digest": catalogue.digest', '"catalogue_digest": None', INS),
    M("in: model digest dropped", "instantiate.py", '"model_digest": None if model is None else model.digest', '"model_digest": None', INS),
    M("in: review status hidden", "instantiate.py", '"review_status": entry["review"]["status"]', '"review_status": "reviewed"', INS),
    M("in: supplied_by hidden", "instantiate.py", '"supplied_by": entry["supplied_by"]', '"supplied_by": "builtin"', INS),
    M("in: instance total wrong", "instantiate.py", "        total += len(inst)", "        total += 0", INS),
    # ---- review_pack.py
    M("rp: reviewer line dropped", "review_pack.py", '        if rv["status"] == "reviewed":', "        if False:", PACK),
    M("rp: count of proposed wrong", "review_pack.py", 'if e["review"]["status"] == "proposed")', 'if e["review"]["status"] != "reviewed")', PACK),
    M("rp: rivals not marked", "review_pack.py", 'f"`{x}`" for x in e["rivals"]', 'x for x in e["rivals"]', PACK),
    M("rp: checks dropped", "review_pack.py", '        out.append(_row("Checks"', '        _ = (_row("Checks"', PACK),
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
