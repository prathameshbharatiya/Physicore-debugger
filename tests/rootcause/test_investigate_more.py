"""INV (M4e, from the mutation run): exact refusal reasons, exit codes and the self-check."""
import copy
import json

import pytest

from physicore_collector.rootcause import investigate as V, view as W
from tests.rootcause.hyp_fx import scenario
from tests.rootcause.test_catalogue import user_file


@pytest.fixture(scope="module")
def sc(tmp_path_factory):
    t = tmp_path_factory.mktemp("m4e_more")
    return {k: scenario(t, k) for k in ("F03", "F02", "F09")} | {"tmp": t}


def tampered(report, tmp, name, f):
    d = json.loads(report.read_text())
    f(d)
    out = tmp / name
    out.write_text(json.dumps(d))
    return out


def problems(sc_, **kw):
    with pytest.raises(V.InvestigationError) as e:
        V.investigate(sc_["report"], **kw)
    return e.value.code, " ".join(e.value.problems)


def errs(rep, f):
    r = copy.deepcopy(rep)
    f(r)
    return " | ".join(V.validate_report(r))


def test_INV_14_each_dependency_refusal_has_its_own_reason(sc, tmp_path):
    nodeps = tampered(sc["F02"]["report"], tmp_path, "nodeps.json", lambda d: d["inputs"].update(dependencies=None))
    with pytest.raises(V.InvestigationError) as e:
        V.investigate(nodeps, dependencies_path=sc["F02"]["deps"])
    assert e.value.code == 3 and "made without a dependency file" in " ".join(e.value.problems)
    other = tmp_path / "o.yaml"
    other.write_text(sc["F02"]["deps"].read_text() + "\n# x\n")
    code, msg = problems(sc["F02"], dependencies_path=other)
    assert code == 3 and "does not have the digest" in msg
    broken = tmp_path / "b.yaml"
    broken.write_text("x: [")
    code, msg = problems(sc["F02"], dependencies_path=broken)
    assert code == 3 and msg.startswith("dependencies:") and "digest" not in msg and "without" not in msg


def test_INV_15_a_broken_or_clashing_catalogue_is_refused(sc, tmp_path):
    bad = tmp_path / "c.yaml"
    bad.write_text("catalogue_schema_version: 1\nentries: [")
    code, msg = problems(sc["F03"], catalogue_path=bad)
    assert code == 3 and msg.startswith("catalogue:") and "did not load" not in msg
    import yaml
    clash = tmp_path / "clash.yaml"
    clash.write_text(yaml.safe_dump(user_file({"id": "command_not_followed"})))
    code, msg = problems(sc["F03"], catalogue_path=clash)
    assert code == 3 and "catalogue" in msg


def test_INV_16_a_not_run_check_for_digests_gives_a_note_and_exit_1(sc, monkeypatch):
    # no dev scenario leaves a generated check pending, so the assignment result is given one that was not run for a digest mismatch
    real = V.K.assign

    def fake(*a, **k):
        out = real(*a, **k)
        out["checks"] = list(out["checks"]) + [{"check_id": "x", "outcome": "not_run", "reason": "digest_mismatch"}]
        return out
    monkeypatch.setattr(V.K, "assign", fake)
    rep = V.investigate(sc["F03"]["report"], dependencies_path=sc["F03"]["deps"])
    assert rep["exit_code"] == 1 and any("generated contract was not run" in p for p in rep["problems"])
    monkeypatch.setattr(V.K, "assign", real)
    ok = V.investigate(sc["F03"]["report"], dependencies_path=sc["F03"]["deps"])
    assert ok["exit_code"] == 0 and not any("generated contract" in p for p in ok["problems"])


def test_INV_17_counts_match_the_units(sc):
    rep = V.investigate(sc["F03"]["report"])
    s = rep["summary"]
    assert s["leaders"] == sum(1 for u in rep["units"] if u["leader"]) >= 1
    assert s["instances"] == sum(len(u["instances"]) for u in rep["units"])
    assert sum(s["claim_levels"].values()) == s["instances"] and sum(s["set_status"].values()) == s["units"]


def test_INV_18_a_report_that_fails_its_own_schema_is_not_returned(sc, monkeypatch):
    monkeypatch.setattr(V, "validate_report", lambda r: ["forced"])
    code, msg = problems(sc["F03"])
    assert code == 3 and msg.startswith("internal:") and "forced" in msg


def test_INV_19_each_schema_rule_names_itself(sc):
    rep = V.investigate(sc["F03"]["report"])
    u = lambda r: r["units"][0]
    i0 = lambda r: u(r)["instances"][0]
    assert "unexpected" in errs(rep, lambda r: u(r).update(extra=1))
    assert "unexpected" in errs(rep, lambda r: r.update(extra=1))
    assert "tier: unknown" in errs(rep, lambda r: i0(r).update(tier="probable"))
    assert "fixed sentence" in errs(rep, lambda r: i0(r).update(claim_sentence="Something else."))
    assert "uncapped" in errs(rep, lambda r: i0(r).update(claim_level=2, uncapped_level=1, claim_sentence=V.K.LEVEL_SENTENCE[2]))
    assert "unknown kind or stance" in errs(rep, lambda r: i0(r)["ledger"].append({"kind": "observed", "stance": "maybe"}))
    assert "never emits" in errs(rep, lambda r: i0(r).update(contrast={"score": 1}))
    # a sentence that a higher level may use is refused at level 0
    assert "not allowed at its level" in errs(rep, lambda r: i0(r).update(claim_level=0, claim_sentence=V.K.LEVEL_SENTENCE[0], would_raise="This is compatible with this mechanism."))


def test_INV_20_view_shows_the_verification_line_and_the_assumptions(sc):
    rep = V.investigate(sc["F02"]["report"], dependencies_path=sc["F02"]["deps"], evidence_path=sc["F02"]["bag"], verify=True)
    md = W.render_markdown(rep)
    assert "Verification against the recording" in md
    r2 = copy.deepcopy(rep)
    r2["verification"] = None
    assert "Verification against the recording" not in W.render_markdown(r2)
    withs = copy.deepcopy(rep)
    withs["units"][0]["assumptions"] = [{"entry_id": "e", "slot": "s", "source": "system_model", "topics": ["/t"]}]
    nos = copy.deepcopy(rep)
    nos["units"][0]["assumptions"] = []
    assert "Assumptions" in W.render_markdown(withs) and "Assumptions" not in W.render_markdown(nos)
