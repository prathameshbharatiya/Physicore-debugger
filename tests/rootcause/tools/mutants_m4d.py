"""Mutants of the M4d module (causal.py). Run: PYTHONPATH=src:. python -m tests.rootcause.tools.mutants_m4d [name fragments]

Justified equivalents (2 of 68):
  - "asg: rivals unsorted": the catalogue loader already sorts every entry's rivals.
  - "asg: reachable flag wrong": the table lists levels 0 to 4 only, and all of them are reachable; level 5 is added separately as not reachable."""
from tests.localization.tools.mutate import Mutant

F = "physicore_collector/rootcause/causal.py"
T = ("tests/rootcause/test_causal.py",)


def M(name, old, new):
    return Mutant(name, F, old, new, T)


MUTANTS = [
    M("ladder: max reachable 5", "MAX_REACHABLE = 4", "MAX_REACHABLE = 5"),
    M("ladder: level 4 sentence stronger", '"The evidence indicates this mechanism, supported by a contrast between these recordings."', '"The evidence confirms this mechanism, supported by a contrast between these recordings."'),
    M("ladder: level 2 sentence stronger", '"The evidence is compatible with this mechanism; it is supported by the listed evidence."', '"The recorded evidence favours this mechanism; it is supported by the listed evidence."'),
    M("ladder: release from level 0", "RELEASED = {1: {\"propagation\"}}", "RELEASED = {0: {\"propagation\"}}"),
    M("ladder: release nothing", "RELEASED = {1: {\"propagation\"}}", "RELEASED = {}"),
    M("ladder: release from level 2", "RELEASED = {1: {\"propagation\"}}", "RELEASED = {2: {\"propagation\"}}"),
    M("gate: stronger words ignored", "        if lvl > level:\n            bad +=", "        if False:\n            bad +="),
    M("gate: level equal counted stronger", "        if lvl > level:\n            bad +=", "        if lvl >= level:\n            bad +="),
    M("gate: forbidden screen dropped", "    bad = [w for w in find_forbidden(text) if not any(", "    bad = [w for w in [] if not any("),
    M("gate: release applies at any level", "for lvl, words in RELEASED.items() if level >= lvl)]", "for lvl, words in RELEASED.items() if True)]"),
    M("gate: stronger table level 3 dropped", '3: ("favours",), ', ""),
    M("gate: stronger table level 4 dropped", '4: ("indicates this mechanism",), ', ""),
    M("gate: stronger table level 2 dropped", '2: ("compatible with this mechanism",), ', ""),
    M("gate: stronger table level 5 dropped", '5: ("confirmed",)}', "5: ()}"),
    M("cmp: detector digest ignored", 'if {k: v for k, v in ia.items() if k != "dependencies"} != {k: v for k, v in ib.items() if k != "dependencies"}:', "if False:"),
    M("cmp: dependency digest ignored", 'if (ia.get("dependencies") or {}).get("sha256") != (ib.get("dependencies") or {}).get("sha256"):', "if False:"),
    M("cmp: parameters ignored", 'if a.get("limits") != b.get("limits"):', "if False:"),
    M("cmp: comparable flag wrong", '"comparable": not diff', '"comparable": True'),
    M("cmp: name wrong", '"detector_configuration_digest"', '"detector_digest"'),
    M("contrast: empty pairs accepted", "    return bool(pairs) and not (pairs & have)", "    return not (pairs & have)"),
    M("contrast: any overlap tolerated", "    return bool(pairs) and not (pairs & have)", "    return bool(pairs) and not (pairs <= have)"),
    M("contrast: category ignored", "    have = {(s[\"node\"], s[\"category\"]) for s in base[\"symptoms\"]}", "    have = {(s[\"node\"], c) for s in base[\"symptoms\"] for c in (p[1] for p in pairs)}"),
    M("iv: before treated as after", "        if ts and all(t > iv[\"at_ns\"] for t in ts):", "        if ts and all(t < iv[\"at_ns\"] for t in ts):"),
    M("iv: any symptom after counts", "        if ts and all(t > iv[\"at_ns\"] for t in ts):", "        if ts and any(t > iv[\"at_ns\"] for t in ts):"),
    M("iv: no times accepted", "        if ts and all(t > iv[\"at_ns\"] for t in ts):", "        if all(t > iv[\"at_ns\"] for t in ts):"),
    M("iv: model ignored", "    if model is None:\n        return None\n    pairs", "    pairs"),
    M("lvl: link not required for level 1", "    if any(x[\"kind\"] == \"observed\" and x.get(\"link_id\") for x in inst[\"ledger\"]):\n        raw = 1", "    if True:\n        raw = 1"),
    M("lvl: unmet prediction ignored", 'unmet = any(p["state"] == "not_observed" for p in inst["predictions"])', "unmet = False"),
    M("lvl: tier not required", '    if inst["tier"] == "supported" and sat and not unmet', '    if sat and not unmet'),
    M("lvl: share not required", ' and E._fr(sat, direct) >= E.MIN_SHARE:\n        raw = max(raw, 2)', ':\n        raw = max(raw, 2)'),
    M("lvl: share strict", "E._fr(sat, direct) >= E.MIN_SHARE:\n        raw = max", "E._fr(sat, direct) > E.MIN_SHARE:\n        raw = max"),
    M("lvl: rivals optional", "        if rivals and all(r in peers", "        if all(r in peers"),
    M("lvl: absent rival counts excluded", "all(r in peers and peers[r][\"tier\"] == \"contradicted\" for r in rivals)", "all(r not in peers or peers[r][\"tier\"] == \"contradicted\" for r in rivals)"),
    M("lvl: rival not contradicted counts", "peers[r][\"tier\"] == \"contradicted\" for r in rivals", "peers[r][\"tier\"] != \"not_assessed\" for r in rivals"),
    M("lvl: baseline comparability ignored", 'if baseline is not None and cmp_ and cmp_["comparable"] and baseline_contrast(inst, baseline):', 'if baseline is not None and baseline_contrast(inst, baseline):'),
    M("lvl: baseline contrast ignored", 'cmp_["comparable"] and baseline_contrast(inst, baseline):\n                raw = 4', 'cmp_["comparable"]:\n                raw = 4'),
    M("lvl: intervention ignored", "            elif intervention_contrast(inst, report, model) is not None:\n                raw = 4", "            elif False:\n                raw = 4"),
    M("lvl: level 4 without level 3", "            raw = 3\n            if baseline", "            raw = 3\n        if True and baseline"),
    M("cap: contradicted cap lost", '    if inst["tier"] == "contradicted":\n        caps.append("contradicted")', "    if False:\n        caps.append(\"contradicted\")"),
    M("cap: blocker cap lost", '    if comp.get("blocker_ids"):', "    if False:"),
    M("cap: blocker only components", 'comp = comps.get(unit_ev["unit_id"], {}) if unit_ev["unit_kind"] == "component" else {}', "comp = {}"),
    M("cap: check cap lost", '    if check_open:\n        caps.append("check_not_run")', "    if False:\n        caps.append(\"check_not_run\")"),
    M("cap: caps at 2", "    level = raw if not caps else min(raw, 1)", "    level = raw if not caps else min(raw, 2)"),
    M("cap: caps ignored", "    level = raw if not caps else min(raw, 1)", "    level = raw"),
    M("cap: baseline note lost", '    if raw >= 3 and baseline is not None and cmp_ is not None and not cmp_["comparable"]:', "    if False:"),
    M("cap: baseline note at level 2", 'if raw >= 3 and baseline is not None and cmp_ is not None and not cmp_["comparable"]:', 'if raw >= 2 and baseline is not None and cmp_ is not None and not cmp_["comparable"]:'),
    M("cap: contradictions list empty", '"contradictions": [x["item_id"] for x in inst["ledger"] if x["stance"] == "contradicts"]', '"contradictions": []'),
    M("asg: raise sentence of next level", '"would_raise": RAISE[level]', '"would_raise": RAISE[min(level + 1, 4)]'),
    M("asg: contrast kind wrong", '{"kind": "baseline"} if baseline is not None and cmp_["comparable"] and baseline_contrast(i, baseline)', '{"kind": "declared_intervention"} if baseline is not None and cmp_["comparable"] and baseline_contrast(i, baseline)'),
    M("asg: requests for leaders", '        if u["set_status"] != "leading":', "        if True:"),
    M("asg: requests only leaders", '        if u["set_status"] != "leading":', '        if u["set_status"] == "leading":'),
    M("asg: contradicted get requests", '                if i["tier"] == "contradicted":\n                    continue', "                pass"),
    M("asg: present tokens requested", '                    if rq["usual_availability"] == "usually_absent":\n                        reqs.append', '                    if True:\n                        reqs.append'),
    M("asg: requests unsorted", "        u[\"diagnostic_asks\"] = sorted(reqs, key=lambda r: (r[\"measurement\"], r[\"entry_id\"]))", "        u[\"diagnostic_asks\"] = reqs"),
    M("asg: rivals unsorted", '"would_discriminate_from": sorted(src["rivals"])', '"would_discriminate_from": list(src["rivals"])'),
    M("asg: input mutated", "    out = copy.deepcopy(ev)", "    out = ev"),
    M("asg: level 5 reachable in table", '"reachable": False}]', '"reachable": True}]'),
    M("asg: reachable flag wrong", '"reachable": n <= MAX_REACHABLE}', '"reachable": True}'),
    M("prop: covered check lost", 'if e["from_coverage"] == "covered" and e["from_symptom_count"] == 0 and e["to_symptom_count"] > 0:', 'if e["from_symptom_count"] == 0 and e["to_symptom_count"] > 0:'),
    M("prop: downstream count ignored", 'if e["from_coverage"] == "covered" and e["from_symptom_count"] == 0 and e["to_symptom_count"] > 0:', 'if e["from_coverage"] == "covered" and e["from_symptom_count"] == 0:'),
    M("prop: both-have reading lost", 'elif e["from_symptom_count"] > 0 and e["to_symptom_count"] > 0:', "elif False:"),
    M("prop: not covered reading lost", 'elif e["from_coverage"] != "covered":', "elif False:"),
    M("prop: unrelated edges included", '        if e["from_node"] not in top and e["to_node"] not in top:\n            continue', "        pass"),
    M("prop: only from end", 'if e["from_node"] not in top and e["to_node"] not in top:', 'if e["from_node"] not in top:'),
    M("prop: unsorted", "    return sorted(out, key=lambda x: x[\"edge_id\"])", "    return out"),
    M("snt: set sentence level", '        out.append({"text": u["set_sentence"], "claim_level": 0})', '        out.append({"text": u["set_sentence"], "claim_level": 5})'),
    M("snt: claim sentence level", '            out.append({"text": i["claim_sentence"], "claim_level": i["claim_level"]})', '            out.append({"text": i["claim_sentence"], "claim_level": 5})'),
    M("snt: cap sentences dropped", "            for c in i[\"cap_sentences\"]:\n                out.append", "            for c in []:\n                out.append"),
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
