"""Mutants of the M4c modules (evaluate.py, checks.py). Run: PYTHONPATH=src:. python -m tests.rootcause.tools.mutants_m4c [name fragments]

Justified equivalent (1 of 70): "ev: leader may be unsupported". An instance that is not supported has a satisfied share of 0, so the minimum-share test refuses it too."""
from tests.localization.tools.mutate import Mutant

P = "physicore_collector/rootcause/"
EV = ("tests/rootcause/test_evaluate.py", "tests/rootcause/test_evaluate_more.py")


def M(name, file, old, new):
    return Mutant(name, P + file, old, new, EV)


MUTANTS = [
    M("ev: min share off", "evaluate.py", 'or top["_share"] < MIN_SHARE:', "or False:"),
    M("ev: min share strict", "evaluate.py", 'or top["_share"] < MIN_SHARE:', 'or top["_share"] <= MIN_SHARE:'),
    M("ev: margin off", "evaluate.py", 'if any(top["_share"] - o["_share"] < MARGIN for o in live[1:]):', "if False:"),
    M("ev: margin strict", "evaluate.py", 'top["_share"] - o["_share"] < MARGIN for', 'top["_share"] - o["_share"] <= MARGIN for'),
    M("ev: blocker ignored", "evaluate.py", "    if blocked:\n", "    if False:\n"),
    M("ev: blocker only components", "evaluate.py", 'blocked = u["unit_kind"] == "component" and bool(', 'blocked = False and bool('),
    M("ev: leader may be unsupported", "evaluate.py", 'if top["tier"] != "supported" or', "if False or"),
    M("ev: contradicted may lead", "evaluate.py", "live = [e for e in ordered if e[\"tier\"] != \"contradicted\"]\n    supported", "live = ordered\n    supported"),
    M("ev: unsupported set not unresolved", "evaluate.py", 'return {"set_status": "unresolved", "leader": None, "reason": "nothing_supported"}', 'return {"set_status": "competing", "leader": None, "reason": "nothing_supported"}'),
    M("ev: empty set status", "evaluate.py", 'return {"set_status": "no_applicable_mechanism", "leader": None, "reason": "no_entry_applies"}', 'return {"set_status": "unresolved", "leader": None, "reason": "no_entry_applies"}'),
    M("ev: order ignores contradiction", "evaluate.py", 'e["tier"] == "contradicted", -e["_share"]', 'False, -e["_share"]'),
    M("ev: order ignores share", "evaluate.py", '-e["_share"], e["_open"]', '0, e["_open"]'),
    M("ev: order ignores open", "evaluate.py", 'e["_open"], e["entry_id"], e["instance_id"]', '0, e["entry_id"], e["instance_id"]'),
    M("ev: order share ascending", "evaluate.py", '-e["_share"], e["_open"]', 'e["_share"], e["_open"]'),
    M("ev: open order descending", "evaluate.py", '-e["_share"], e["_open"], e["entry_id"]', '-e["_share"], -e["_open"], e["entry_id"]'),
    M("ev: tier contradicted lost", "evaluate.py", '    if contra:\n        tier, status = "contradicted", "assessed"', '    if False:\n        tier, status = "contradicted", "assessed"'),
    M("ev: not assessed lost", "evaluate.py", "    elif not assessable:", "    elif False:"),
    M("ev: supported needs nothing", "evaluate.py", "    elif sat:\n        tier, status = \"supported\"", "    elif True:\n        tier, status = \"supported\""),
    M("ev: restated counted direct", "evaluate.py", 'not in ("not_evaluable", "restates_symptom")]', 'not in ("not_evaluable",)]'),
    M("ev: own topics not restated", "evaluate.py", 'elif inst["slot_selects"][r["slot"]] == "symptom_topics":', "elif False:"),
    M("ev: absent satisfied by silence", "evaluate.py", 'state = "contradicted" if hit else "absence_not_established"', 'state = "contradicted" if hit else "satisfied"'),
    M("ev: absent hit not contradiction", "evaluate.py", 'state = "contradicted" if hit else "absence_not_established"', 'state = "satisfied" if hit else "absence_not_established"'),
    M("ev: present miss counted as contradiction", "evaluate.py", 'state = "satisfied" if hit else "not_observed"', 'state = "satisfied" if hit else "contradicted"'),
    M("ev: unbound slot not missing", "evaluate.py", '            if not topics:\n                state = "binding_missing"', '            if False:\n                state = "binding_missing"'),
    M("ev: link label ignored", "evaluate.py", 'any(lk["label"] == "consistent_with_propagation" for lk in related[s["symptom_id"]])', "True"),
    M("ev: bad labels ignored", "evaluate.py", 'any(lk["label"] in ("inconsistent_order", "outside_declared_window") for lk in related[s["symptom_id"]])', "False"),
    M("ev: window label not bad", "evaluate.py", '("inconsistent_order", "outside_declared_window")', '("inconsistent_order",)'),
    M("ev: link direction swapped", "evaluate.py", 'return [lk for lk in links if lk["from_symptom"] in unit_syms and lk["to_symptom"] == s["symptom_id"]]', 'return [lk for lk in links if lk["to_symptom"] in unit_syms and lk["from_symptom"] == s["symptom_id"]]'),
    M("ev: upstream direction swapped", "evaluate.py", 'return [lk for lk in links if lk["to_symptom"] in unit_syms and lk["from_symptom"] == s["symptom_id"]]', 'return [lk for lk in links if lk["from_symptom"] in unit_syms and lk["to_symptom"] == s["symptom_id"]]'),
    M("ev: down flag wrong", "evaluate.py", 'down = sel == "declared_downstream_topics"', 'down = sel != "declared_downstream_topics"'),
    M("ev: link path only downstream", "evaluate.py", 'sel in ("declared_downstream_topics", "declared_upstream_topics") and hit', 'sel in ("declared_downstream_topics",) and hit'),
    M("ev: violated check not support", "evaluate.py", '            elif c["outcome"] == "violated":\n                state = "satisfied"', '            elif c["outcome"] == "violated":\n                state = "check_open"'),
    M("ev: satisfied check not contradiction", "evaluate.py", '                state = "contradicted"\n                item("inferred", "contradicts", pid, rule="generated_contract_check"', '                state = "check_open"\n                item("inferred", "contradicts", pid, rule="generated_contract_check"'),
    M("ev: open outcomes as support", "evaluate.py", 'c["outcome"] in ("not_runnable", "not_run", "insufficient_evidence", "incomparable_clock_domains") or', 'c["outcome"] in ("not_runnable",) or'),
    M("ev: binding reason lost", "evaluate.py", 'state = "binding_missing" if (c is not None and c["reason"] == "binding_missing") else "check_open"', 'state = "check_open"'),
    M("ev: m3d symptom evidence lost", "evaluate.py", '                if c["source"] == "m3d_report":', "                if False:"),
    M("ev: missing evidence only absent", "evaluate.py", 'if rq["usual_availability"] == "usually_absent":', "if True:"),
    M("ev: missing evidence from contradicted", "evaluate.py", 'for e in live for it in e["ledger"] if it["kind"] == "unconfirmed" and "token" in it', 'for e in ordered for it in e["ledger"] if it["kind"] == "unconfirmed" and "token" in it'),
    M("ev: assumptions not recorded", "evaluate.py", 'if src in ("system_model", "dependency_file"):', "if False:"),
    M("ev: order numbers missing", "evaluate.py", '            e["order"] = n', "            pass"),
    M("ev: share text wrong", "evaluate.py", '"satisfied_share": f"{sat}/{len(direct)}" if direct else "0/0"', '"satisfied_share": f"{sat}/{len(preds)}" if direct else "0/0"'),
    M("ev: leader flag missing in score input", "evaluate.py", '"tier": ("leading" if i["instance_id"] == u["leader"] else i["tier"])', '"tier": i["tier"]'),
    M("ev: unresolved member missing", "evaluate.py", '        hy.append({"class": "unresolved"', '        _x = ({"class": "unresolved"'),
    M("ev: sensitivity skips edges", "evaluate.py", '(src == "dependency_file" and not dependency_edges)', "(False)"),
    M("ev: sensitivity always robust", "evaluate.py", '"robust": sa == sb', '"robust": True'),
    M("ev: sensitivity changed units empty", "evaluate.py", '"changed_units": sorted(k for k in sa if sa[k] != sb.get(k))', '"changed_units": []'),
    M("ev: sensitivity model not dropped", "evaluate.py", '("system_model", None, list(dependency_edges))', '("system_model", model, list(dependency_edges))'),
    M("ev: signature ignores leader", "evaluate.py", 'next((i["entry_id"] for i in u["instances"] if i["instance_id"] == u["leader"]), None)', "None"),
    # ---- checks.py
    M("ck: m3d symptom not violation", "checks.py", 'st.update(outcome="violated", source="m3d_report", symptom_ids=syms)', 'st.update(outcome="satisfied", source="m3d_report", symptom_ids=syms)'),
    M("ck: silence read as pass", "checks.py", 'st.update(outcome="insufficient_evidence", reason="no_symptom", source="m3d_report")', 'st.update(outcome="satisfied", reason="no_symptom", source="m3d_report")'),
    M("ck: unbound check runnable", "checks.py", "                if not topics:\n", "                if False:\n"),
    M("ck: range min lost", "checks.py", '                            if c["min"] is not None:\n                                check["min"] = c["min"]', "                            pass"),
    M("ck: range max lost", "checks.py", '                            if c["max"] is not None:\n                                check["max"] = c["max"]', "                            pass"),
    M("ck: constraint topic unchecked", "checks.py", 'if b is not None and b["topic"] in topics and b["field"] is not None:', 'if b is not None and b["field"] is not None:'),
    M("ck: field optional", "checks.py", 'and b["topic"] in topics and b["field"] is not None:', 'and b["topic"] in topics:'),
    M("ck: contract unsorted", "checks.py", '"expectations": [uniq[k] for k in sorted(uniq)]}', '"expectations": [uniq[k] for k in uniq]}'),
    M("ck: contract hash dropped", "checks.py", '"contract_sha256": None if contract is None else _h(contract)}', '"contract_sha256": None}'),
    M("ck: digest check off", "checks.py", "            if not digests_match(report, inv):", "            if False:"),
    M("ck: digest empty accepted", "checks.py", "    return bool(want) and want == have", "    return want == have"),
    M("ck: digest order sensitive", "checks.py", "want = sorted((e[\"artifact_ref\"], e[\"sha256\"]) for e in report[\"evidence\"])", "want = [(e[\"artifact_ref\"], e[\"sha256\"]) for e in report[\"evidence\"]][::-1]"),
    M("ck: violated not dominant", "checks.py", '    if "violated" in statuses:\n        return "violated"', "    if False:\n        return \"violated\""),
    M("ck: all-satisfied loose", "checks.py", 'if statuses and all(s == "satisfied" for s in statuses):', 'if statuses and any(s == "satisfied" for s in statuses):'),
    M("ck: status map no divergence", "checks.py", '"no_divergence_observed_in_covered_window": "satisfied"', '"no_divergence_observed_in_covered_window": "violated"'),
    M("ck: status map divergence", "checks.py", '"divergence_found": "violated"', '"divergence_found": "satisfied"'),
    M("ck: incomparable mapped wrong", "checks.py", '"incomparable_timebases": "incomparable_clock_domains"', '"incomparable_timebases": "insufficient_evidence"'),
    M("ck: no recording reason", "checks.py", '        elif evidence is None:\n            why = "no_recording"', '        elif evidence is None:\n            why = "digest_mismatch"'),
    M("ck: pending not-run lost", "checks.py", '            if why:\n                c.update(outcome="not_run", reason=why)', '            if False:\n                c.update(outcome="not_run", reason=why)'),
    M("ck: unknown expectation as pass", "checks.py", 'res.get(e, "not_runnable") for e in c["expectation_ids"]', 'res.get(e, "satisfied") for e in c["expectation_ids"]'),
    M("ck: event kind unsupported", "checks.py", '                elif k["kind"] == "event_response":', '                elif False:'),
    M("ck: range without model runnable", "checks.py", '                elif k["kind"] == "range":\n                    st.update(outcome="not_runnable", reason="no_constraint", source="none")', '                elif k["kind"] == "range":\n                    st.update(source="generated_contract")'),
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
