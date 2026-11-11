"""X-19..X-25, X-01/02, X-08, X-14, CS-29 end-to-end through the real command (TEST_PLAN 10)."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from physicore_collector.anomaly import report as R
from physicore_collector.anomaly.findings import AnomalyReport
from physicore_collector.cli import app
from physicore_collector.inventory import exit_code as inv_exit, inspect_evidence
from tests import investigator_fixtures as F
from tests.anomaly import fixtures as X

RCL = "rcl_interfaces/msg/Log"
RUNNER = CliRunner()
ROOT = Path(__file__).resolve().parents[2]
LIMITS = {"max_retained_observations": 5, "max_text_bytes": 64, "max_findings_per_detector": 100}
CMD = ["evidence", "detect-anomalies"]


def errors(n=3, level=40, start=F.BASE):
    return [(start + i * F.MS, {"level": level, "name": "nav", "msg": "boom"}) for i in range(n)]


def make_bag(tmp, msgs, topic="/rosout", typ=RCL, name="bag", extra=None):
    streams = {topic: msgs, **(extra or {})}
    return X.write_typed_bag(tmp / name, [streams], {t: typ for t in streams})


def write_cfg(tmp, detectors, name="cfg.json", limits=None):
    p = tmp / name
    p.write_text(json.dumps({"anomaly_config_version": 1, "limits": limits or LIMITS, "detectors": detectors}))
    return p


LOG_ON = {"tierA.ros_log": {"enabled": True, "topics": "auto"}}


def invoke(args, **kw):
    return RUNNER.invoke(app, args, **kw)


def run_json(bag, cfgp, *extra):
    r = invoke([*CMD, str(bag), "--anomaly-config", str(cfgp), "--json", *extra])
    return r, (json.loads(r.output) if r.output.strip().startswith("{") else None)


def res(rep, detector_id):
    return next(x for x in rep["results"] if x["detector_id"] == detector_id)


def tree(root: Path):
    out = {}
    for p in [root, *sorted(root.rglob("*"))]:
        out[p.relative_to(root).as_posix()] = (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
    return out


@pytest.fixture
def clean_bag(tmp_path):
    bag = make_bag(tmp_path, errors())
    assert inv_exit(inspect_evidence(bag)) == 0
    return bag


# ---------------------------------------------------------------------------------------------------- X-19 exit-code table
def test_X19a_findings_without_flag_exit_zero(tmp_path, clean_bag):
    r, rep = run_json(clean_bag, write_cfg(tmp_path, LOG_ON))
    assert r.exit_code == 0 and rep["exit_code"] == 0 and rep["exit_reasons"] == [] and len(rep["findings"]) == 1


def test_X19b_findings_with_flag_exit_one(tmp_path, clean_bag):
    r, rep = run_json(clean_bag, write_cfg(tmp_path, LOG_ON), "--fail-on-findings")
    assert r.exit_code == 1 and rep["exit_code"] == 1 and rep["exit_reasons"] == ["findings_with_fail_on_findings"]


def test_X19c_no_findings_with_flag_exit_zero(tmp_path):
    bag = make_bag(tmp_path, errors(level=20))
    r, rep = run_json(bag, write_cfg(tmp_path, LOG_ON), "--fail-on-findings")
    assert r.exit_code == 0 and rep["findings"] == [] and res(rep, "tierA.ros_log")["status"] == "no_anomaly_observed" and rep["exit_reasons"] == []


@pytest.mark.parametrize("as_json", [False, True])
def test_X19d_invalid_config_exit_three_nothing_analysed(tmp_path, clean_bag, monkeypatch, as_json):
    from physicore_collector.anomaly import service
    monkeypatch.setattr(service, "inspect_evidence", lambda *a, **k: pytest.fail("evidence must not be analysed with an invalid configuration"))
    cfgp = write_cfg(tmp_path, {"tierB.publishing_gap": {"enabled": True, "topics": ["/rosout"]}})        # required numeric parameters missing
    r = invoke([*CMD, str(clean_bag), "--anomaly-config", str(cfgp)] + (["--json"] if as_json else []))
    assert r.exit_code == 3
    if as_json:
        rep = json.loads(r.output)
        assert rep["exit_code"] == 3 and rep["exit_reasons"] == ["invalid_config"] and rep["config_problems"] and rep["results"] == [] and rep["findings"] == []
    else:
        assert "Invalid anomaly configuration" in r.output and "invalid_config" in r.output


def test_X19d2_unreadable_or_malformed_config_file(tmp_path, clean_bag):
    bad = tmp_path / "bad.yaml"
    bad.write_text("detectors: [unterminated")
    assert invoke([*CMD, str(clean_bag), "--anomaly-config", str(bad)]).exit_code == 3
    assert invoke([*CMD, str(clean_bag), "--anomaly-config", str(tmp_path / "missing.yaml")]).exit_code == 3


def test_X19e_21_only_non_finding_statuses_exit_zero_even_with_flag(tmp_path, clean_bag):
    dets = {"tierA.goal_status": {"enabled": True, "topics": "auto"},                                       # not_applicable
            "tierB.publishing_gap": {"enabled": True, "topics": ["/rosout"], "min_intervals": 50, "multiplier": "3", "absolute_floor_ns": 1},   # insufficient
            "tierB.array_length": {"enabled": True, "rules": [{"topic": "/rosout", "path": "msg", "expected_length": 3}]}}                       # blind spot
    r, rep = run_json(clean_bag, write_cfg(tmp_path, dets), "--fail-on-findings")
    st = {x["detector_id"]: x["status"] for x in rep["results"]}
    assert r.exit_code == 0 and rep["findings"] == []
    assert st["tierA.goal_status"] == "not_applicable" and st["tierB.publishing_gap"] == "insufficient_evidence" and st["tierB.array_length"] == "blind_spot"


def test_X19f_evidence_not_inspectable_exit_two_with_report(tmp_path):
    r, rep = run_json(tmp_path / "does-not-exist", write_cfg(tmp_path, LOG_ON))
    assert r.exit_code == 2 and rep["exit_code"] == 2 and rep["exit_reasons"] == ["evidence_not_inspectable"]
    one = res(rep, "tierA.ros_log")
    assert one["status"] == "blind_spot" and one["reasons"][0]["code"] == "evidence_not_inspectable"
    assert res(rep, "tierA.battery")["status"] == "not_configured" and len(rep["results"]) == 9


def tampered_incident(tmp_path, synth_profile):
    from tests.test_incident import prep
    inc = prep(make_bag(tmp_path / "src", errors(), name="bagm"), synth_profile, tmp_path / "inc").incident_dir
    seg = next(inc.rglob("*.mcap"))
    raw = bytearray(seg.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    seg.write_bytes(bytes(raw))
    assert inspect_evidence(inc).integrity.state == "tampered"
    return inc


def test_X19g_tampered_evidence_exit_one_report_produced(tmp_path, synth_profile):
    inc = tampered_incident(tmp_path, synth_profile)
    r, rep = run_json(inc, write_cfg(tmp_path, LOG_ON))
    assert r.exit_code == 1 and rep["exit_code"] == 1 and rep["exit_reasons"] == ["evidence_integrity_or_completeness"] and rep["results"]


def test_X19h_partial_with_flag_and_findings_lists_both_reasons(tmp_path):
    bag = make_bag(tmp_path, errors())
    seg = next(bag.glob("*.mcap"))
    seg.write_bytes(seg.read_bytes() + b"y")
    assert inv_exit(inspect_evidence(bag)) == 1
    r, rep = run_json(bag, write_cfg(tmp_path, LOG_ON), "--fail-on-findings")
    assert r.exit_code == 1 and rep["exit_reasons"] == ["evidence_integrity_or_completeness", "findings_with_fail_on_findings"]
    assert rep["findings"] == [] or all(f["coverage"]["topic_coverage"] in ("complete", "incomplete") for f in rep["findings"])


def test_X19i_usage_errors_exit_two(tmp_path, clean_bag):
    cfgp = write_cfg(tmp_path, LOG_ON)
    assert invoke([*CMD]).exit_code == 2
    assert invoke([*CMD, str(clean_bag)]).exit_code == 2                                    # missing --anomaly-config
    assert invoke([*CMD, "--anomaly-config", str(cfgp)]).exit_code == 2                    # missing PATH
    assert invoke([*CMD, str(clean_bag), "--anomaly-config", str(cfgp), "--no-such-option"]).exit_code == 2
    assert invoke([*CMD, str(clean_bag), "--anomaly-config", str(cfgp), "--detector", "tierX.nope"]).exit_code == 2


def test_detector_filter_runs_only_the_named_detector(tmp_path, clean_bag):
    dets = {**LOG_ON, "tierB.non_finite": {"enabled": True, "topics": ["/rosout"], "include_paths": [], "exclude_paths": []}}
    r, rep = run_json(clean_bag, write_cfg(tmp_path, dets), "--detector", "tierB.non_finite")
    assert [x["detector_id"] for x in rep["results"]] == ["tierB.non_finite"] and rep["findings"] == []


# ---------------------------------------------------------------------------------------------------- X-20 / X-22 / X-24 / X-25
def test_X20_24_read_only_and_stdout_only(tmp_path, clean_bag, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    cfgp = write_cfg(tmp_path, LOG_ON)
    before = tree(clean_bag)
    for extra in ([], ["--json"], ["--fail-on-findings"]):
        invoke([*CMD, str(clean_bag), "--anomaly-config", str(cfgp), *extra])
    invoke([*CMD, str(clean_bag), "--anomaly-config", str(tmp_path / "nope.json")])
    assert tree(clean_bag) == before and list(cwd.iterdir()) == []


def test_X22_exact_top_level_keys(tmp_path, clean_bag):
    r, rep = run_json(clean_bag, write_cfg(tmp_path, LOG_ON))
    assert set(rep) == {"anomaly_schema_version", "tool_version", "config_hash", "evidence", "clock_domains", "ordering_key", "severity_disclaimer",
                        "limitations", "exit_code", "exit_reasons", "results", "findings"}
    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)
    assert not [k for k in keys(rep) if "confidence" in k or k == "late" or k.startswith("late_")]
    assert "late_message" not in {f["anomaly_category"] for f in rep["findings"]}
    assert AnomalyReport.model_validate_json(r.output)


def test_X25_exit_code_in_report_equals_process_exit_code(tmp_path, clean_bag, synth_profile):
    cfgp = write_cfg(tmp_path, LOG_ON)
    cases = [(clean_bag, cfgp, []), (clean_bag, cfgp, ["--fail-on-findings"]), (tmp_path / "gone", cfgp, []),
             (clean_bag, write_cfg(tmp_path, {"tierB.publishing_gap": {"enabled": True, "topics": ["/rosout"]}}, name="bad.json"), []), (tampered_incident(tmp_path, synth_profile), cfgp, [])]
    seen = set()
    for bag, c, extra in cases:
        r, rep = run_json(bag, c, *extra)
        assert rep["exit_code"] == r.exit_code
        seen.add(r.exit_code)
    assert seen == {0, 1, 2, 3}


def test_evidence_identity_lists_real_digests(tmp_path, clean_bag):
    r, rep = run_json(clean_bag, write_cfg(tmp_path, LOG_ON))
    on_disk = {hashlib.sha256(p.read_bytes()).hexdigest() for p in clean_bag.glob("*.mcap")}
    assert {e["sha256"] for e in rep["evidence"]} == on_disk


# ---------------------------------------------------------------------------------------------------- X-01 / X-02 determinism
def test_X01_byte_identical_across_runs_hashseeds_and_cwd(tmp_path, clean_bag):
    cfgp = write_cfg(tmp_path, {**LOG_ON, "tierB.non_finite": {"enabled": True, "topics": ["/rosout"], "include_paths": [], "exclude_paths": []}})
    outs = set()
    for i, seed in enumerate(("0", "1", "random", "4242", "7")):
        cwd = tmp_path / f"cwd{i}"
        cwd.mkdir()
        p = subprocess.run([sys.executable, "-m", "physicore_collector", *CMD, str(clean_bag), "--anomaly-config", str(cfgp), "--json"], cwd=cwd,
                           env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, timeout=120)
        assert p.returncode == 0, p.stderr.decode()[-500:]
        outs.add(p.stdout)
    assert len(outs) == 1


def test_X02_detector_order_in_config_does_not_matter(tmp_path, clean_bag):
    d1 = {"tierA.ros_log": LOG_ON["tierA.ros_log"], "tierB.non_finite": {"enabled": True, "topics": ["/rosout"], "include_paths": [], "exclude_paths": []}}
    d2 = dict(reversed(list(d1.items())))
    a = invoke([*CMD, str(clean_bag), "--anomaly-config", str(write_cfg(tmp_path, d1, "a.json")), "--json"]).output
    b = invoke([*CMD, str(clean_bag), "--anomaly-config", str(write_cfg(tmp_path, d2, "b.json")), "--json"]).output
    assert a == b


# ---------------------------------------------------------------------------------------------------- X-08 / X-14
def all_states_report(tmp_path):
    bag = make_bag(tmp_path, errors())
    dets = {"tierA.ros_log": LOG_ON["tierA.ros_log"],                                                                                  # findings_reported
            "tierA.goal_status": {"enabled": True, "topics": "auto"},                                                                  # not_applicable
            "tierB.publishing_gap": {"enabled": True, "topics": ["/rosout"], "min_intervals": 50, "multiplier": "3", "absolute_floor_ns": 1},   # insufficient
            "tierB.array_length": {"enabled": True, "rules": [{"topic": "/rosout", "path": "msg", "expected_length": 3}]},             # blind_spot
            "tierB.non_finite": {"enabled": True, "topics": ["/rosout"], "include_paths": [], "exclude_paths": []}}                    # no_anomaly_observed
    return bag, write_cfg(tmp_path, dets)


def test_X08_six_states_are_distinct_in_json_and_text(tmp_path):
    bag, cfgp = all_states_report(tmp_path)
    r, rep = run_json(bag, cfgp)
    st = {x["detector_id"]: x["status"] for x in rep["results"]}
    assert st == {"tierA.battery": "not_configured", "tierA.diagnostics": "not_configured", "tierA.goal_status": "not_applicable",
                  "tierA.ros_log": "findings_reported", "tierB.array_length": "blind_spot", "tierB.non_finite": "no_anomaly_observed",
                  "tierB.publishing_gap": "insufficient_evidence", "tierB.rate_change": "not_configured", "tierB.stamp_order": "not_configured"}
    assert len(set(st.values())) == 6
    text = invoke([*CMD, str(bag), "--anomaly-config", str(cfgp)]).output
    for line_id, status in (("tierA.ros_log", "findings_reported"), ("tierA.goal_status", "not_applicable"), ("tierB.array_length", "blind_spot"),
                            ("tierB.non_finite", "no_anomaly_observed"), ("tierB.publishing_gap", "insufficient_evidence"), ("tierA.battery", "not_configured")):
        assert any(l.startswith(line_id) and f": {status}" in l for l in text.splitlines()), (line_id, status)


def test_X14_generated_text_has_no_causal_wording(tmp_path):
    bag, cfgp = all_states_report(tmp_path)
    r, rep = run_json(bag, cfgp)
    report = AnomalyReport.model_validate_json(r.output)
    assert R.check_wording(report) == []
    text = invoke([*CMD, str(bag), "--anomaly-config", str(cfgp)]).output
    assert "Severity is an ordered label" in text and "Limitations:" in text


# ---------------------------------------------------------------------------------------------------- CS-29 example config
def test_CS29_example_config_runs_through_the_cli(tmp_path, clean_bag):
    r = invoke([*CMD, str(clean_bag), "--anomaly-config", str(ROOT / "examples" / "anomaly" / "anomaly.example.yaml"), "--json"])
    rep = json.loads(r.output)
    assert r.exit_code == 0 and "config_problems" not in rep
    assert {x["detector_id"]: x["status"] for x in rep["results"]}["tierA.ros_log"] == "findings_reported"


# ---------------------------------------------------------------------------------------------------- X-23 / PRES-05
GOLD = Path(__file__).parent / "golden"


def _help(args):
    r = RUNNER.invoke(app, args, env={"COLUMNS": "100", "NO_COLOR": "1", "TERM": "dumb"})
    return f"exit={r.exit_code}\n" + r.output


@pytest.mark.parametrize("name,args", [("evidence_inventory", ["evidence", "inventory", "--help"]), ("evidence_investigate", ["evidence", "investigate", "--help"]),
                                       ("evidence_decode", ["evidence", "decode", "--help"]), ("top", ["--help"])])
def test_X23_PRES05_existing_help_is_byte_identical_to_baseline(name, args):
    assert _help(args) == (GOLD / f"cli_help_{name}.txt").read_text()


def test_X23_PRES05_new_command_only_under_evidence():
    group = _help(["evidence", "--help"])
    base = (GOLD / "cli_help_evidence_group.txt").read_text()
    for c in ("inventory", "investigate", "decode"):
        assert c in base and c in group
    assert "detect-anomalies" in group and "detect-anomalies" not in base
    assert "detect-anomalies" not in (GOLD / "cli_help_top.txt").read_text() and "detect-anomalies" not in _help(["--help"])
    assert group.index("decode") < group.index("detect-anomalies")


def test_X23_existing_exit_codes_unchanged(tmp_path, clean_bag):
    assert invoke(["evidence", "inventory", str(clean_bag)]).exit_code == 0
    assert invoke(["evidence", "inventory", str(tmp_path / "nope")]).exit_code == 2
    assert invoke(["evidence", "decode", str(clean_bag), "--topic", "/rosout"]).exit_code == 0
    assert invoke(["evidence", "decode", str(clean_bag), "--topic", "/missing"]).exit_code == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    assert invoke(["evidence", "investigate", str(clean_bag), "--expectations", str(bad)]).exit_code == 3
