"""Mutants of the M4e modules (investigate.py, view.py). Run: PYTHONPATH=src:. python -m tests.rootcause.tools.mutants_m4e [name fragments]"""
from tests.localization.tools.mutate import Mutant

P = "physicore_collector/rootcause/"
T = ("tests/rootcause/test_investigate_more.py", "tests/rootcause/test_investigate.py", "tests/rootcause/test_view.py", "tests/rootcause/test_example_hypotheses.py")


def M(name, file, old, new):
    return Mutant(name, P + file, old, new, T)


MUTANTS = [
    M("iv: report unreadable code", "investigate.py", "2 if loaded.unreadable else 3", "3"),
    M("iv: report invalid code", "investigate.py", "2 if loaded.unreadable else 3", "2"),
    M("iv: dependency digest unchecked", "investigate.py", "        if ld.sha256 != want:", "        if False:"),
    M("iv: dependency without report digest accepted", "investigate.py", "        if want is None:\n            raise", "        if False:\n            raise"),
    M("iv: dependency problems ignored", "investigate.py", "        if ld.graph is None:\n            raise", "        if False:\n            raise"),
    M("iv: missing dependency note lost", "investigate.py", "    elif want is not None:\n        problems.append", "    elif False:\n        problems.append"),
    M("iv: model invalid accepted", "investigate.py", "        if lm.model is None:\n            raise", "        if False:\n            raise"),
    M("iv: catalogue invalid accepted", "investigate.py", "        if uc.entries is None:\n            raise", "        if False:\n            raise"),
    M("iv: rival error swallowed", "investigate.py", "        except ValueError as e:\n            raise InvestigationError(3,", "        except ValueError as e:\n            raise InvestigationError(0,"),
    M("iv: evidence path unchecked", "investigate.py", "    if evidence_path is not None and not Path(evidence_path).exists():", "    if False:"),
    M("iv: verify without evidence allowed", "investigate.py", "    if verify and evidence_path is None:", "    if False:"),
    M("iv: mismatch not exit 1", "investigate.py", '        code = 1 if r["summary"]["mismatch"] else 0', "        code = 0"),
    M("iv: not-run digest note lost", "investigate.py", '    if "digest_mismatch" in checks_not_run:', "    if False:"),
    M("iv: not-run code lost", "investigate.py", "        code = max(code, 1)", "        code = code"),
    M("iv: baseline not loaded", "investigate.py", "    if baseline_path is not None:\n        baseline =", "    if False:\n        baseline ="),
    M("iv: leaders miscounted", "investigate.py", '"leaders": sum(1 for u in out["units"] if u["leader"])', '"leaders": 0'),
    M("iv: review counts wrong", "investigate.py", 'for k in ("proposed", "reviewed", "user_supplied")}', 'for k in ("proposed", "reviewed", "user_supplied")} | {"proposed": 0}'),
    M("iv: dependencies supplied flag", "investigate.py", '"supplied": dependencies_path is not None}', '"supplied": True}'),
    M("iv: evidence supplied flag", "investigate.py", '"evidence": {"supplied": evidence_path is not None}', '"evidence": {"supplied": True}'),
    M("iv: self-check skipped", "investigate.py", "    if errs:\n        raise InvestigationError(3, [\"internal", "    if False:\n        raise InvestigationError(3, [\"internal"),
    M("iv: unknown format accepted", "investigate.py", "    if fmt not in FORMATS:\n        return", "    if False:\n        return"),
    M("iv: error text kept", "investigate.py", '        return {"exit_code": e.code, "problems": e.problems, "text": ""}', '        return {"exit_code": e.code, "problems": e.problems, "text": "x"}'),
    M("iv: edges src dst swapped", "investigate.py", 'out.append({"from": a, "to": b})', 'out.append({"from": b, "to": a})'),
    M("iv: binding nodes ignored", "investigate.py", "topics.get(e.src, {e.src} if e.src.startswith(\"/\") else set())", "{e.src} if e.src.startswith(\"/\") else set()"),
    M("iv: non-topic nodes kept", "investigate.py", '{e.dst} if e.dst.startswith("/") else set())', "{e.dst})"),
    # schema
    M("sc: version unchecked", "investigate.py", '    if r["investigation_report_version"] != REPORT_VERSION:', "    if False:"),
    M("sc: exit code range", "investigate.py", "or r[\"exit_code\"] not in (0, 1):", "or False:"),
    M("sc: bool exit code", "investigate.py", 'if not _is_int(r["exit_code"]) or', "if not isinstance(r[\"exit_code\"], int) or"),
    M("sc: set status unchecked", "investigate.py", '        if u["set_status"] not in SETS:', "        if False:"),
    M("sc: leader membership unchecked", "investigate.py", '        if u["leader"] is not None and not any(', "        if False and not any("),
    M("sc: tier unchecked", "investigate.py", '            if i["tier"] not in TIERS:', "            if False:"),
    M("sc: level range", "investigate.py", "or not 0 <= i[\"claim_level\"] <= K.MAX_REACHABLE:", "or False:"),
    M("sc: fixed sentence unchecked", "investigate.py", '            elif i["claim_sentence"] != K.LEVEL_SENTENCE[i["claim_level"]]:', "            elif False:"),
    M("sc: cap order unchecked", "investigate.py", 'and i["claim_level"] > i["uncapped_level"]:', 'and False:'),
    M("sc: contradicted level unchecked", "investigate.py", '            if i["tier"] == "contradicted" and _is_int(i["claim_level"]) and i["claim_level"] > 1:', "            if False:"),
    M("sc: leader support unchecked", "investigate.py", '            if u["leader"] == i["instance_id"] and i["tier"] != "supported":', "            if False:"),
    M("sc: ledger kind unchecked", "investigate.py", 'x.get("kind") not in ("observed", "inferred", "assumption", "unconfirmed") or', "False or"),
    M("sc: ledger stance unchecked", "investigate.py", 'or x.get("stance") not in ("supports", "contradicts", "neutral", "missing"):', "or False:"),
    M("sc: forbidden keys unchecked", "investigate.py", "    if find_forbidden_keys(r):", "    if False:"),
    M("sc: sentences unchecked", "investigate.py", '    errs += [f"sentence: {w!r} is not allowed at its level" for w in _sentence_violations(r)]', "    pass"),
    M("sc: claim sentence gate level", "investigate.py", "            for t in [i[\"claim_sentence\"], i[\"would_raise\"], *i[\"cap_sentences\"]]:\n                bad += K.gate(t, i[\"claim_level\"])", "            for t in [i[\"claim_sentence\"], i[\"would_raise\"], *i[\"cap_sentences\"]]:\n                bad += K.gate(t, 5)"),
    M("sc: unit keys not strict", "investigate.py", "        if not _obj(u, unit_keys, p, errs):", "        if not _obj(u, tuple(u), p, errs):"),
    M("sc: top keys not strict", "investigate.py", '    if not _obj(r, TOP_KEYS, "$", errs):', '    if not _obj(r, tuple(r) if isinstance(r, dict) else TOP_KEYS, "$", errs):'),
    # view
    M("vw: markdown data not escaped", "view.py", 'return md_escape(v, None) if kind == "f" else md_escape(v)', 'return v'),
    M("vw: html data not escaped", "view.py", 'return html_escape(v, None) if kind == "f" else html_escape(v)', 'return v'),
    M("vw: data not truncated", "view.py", 'return md_escape(v, None) if kind == "f" else md_escape(v)', 'return md_escape(v, None)'),
    M("vw: fixed truncated", "view.py", 'return md_escape(v, None) if kind == "f" else md_escape(v)', 'return md_escape(v)'),
    M("vw: html fixed truncated", "view.py", 'return html_escape(v, None) if kind == "f" else html_escape(v)', 'return html_escape(v)'),
    M("vw: security policy dropped", "view.py", "default-src 'none'; style-src 'unsafe-inline'", "default-src *"),
    M("vw: badge level class", "view.py", 'class="badge L{int(b[1])}"', 'class="badge L0"'),
    M("vw: unit heading count", "view.py", 'f"Unit {n} of {len(r[\'units\'])}: {\', \'.join(u[\'topics\'])}"', 'f"Unit {n}: {\', \'.join(u[\'topics\'])}"'),
    M("vw: ledger table dropped", "view.py", '            if i["ledger"]:\n                B.append(("table"', '            if False:\n                B.append(("table"'),
    M("vw: caps dropped", "view.py", '            for c in i["cap_sentences"]:\n                B.append(("p", _fixed(c)))', "            pass"),
    M("vw: would raise dropped", "view.py", '            B.append(("p", _fixed("What would raise it: " + i["would_raise"])))', "            pass"),
    M("vw: limits dropped", "view.py", '    B.append(("list", [_fixed(x) for x in r["limitations"]]))', "    pass"),
    M("vw: sensitivity text", "view.py", '"the verdicts did not change." if x["robust"] else "the verdicts changed for "', '"the verdicts did not change." if not x["robust"] else "the verdicts changed for "'),
    M("vw: empty sentence dropped", "view.py", '    if not r["units"]:\n        B.append', "    if False:\n        B.append"),
    M("vw: asks only when present", "view.py", '        if u["diagnostic_asks"]:', "        if False:"),
    M("vw: assumptions dropped", "view.py", '        if u["assumptions"]:', "        if False:"),
    M("vw: notes dropped", "view.py", '    if r["problems"]:', "    if False:"),
    M("vw: verification line dropped", "view.py", '    if r["verification"] is not None:', "    if False:"),
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
