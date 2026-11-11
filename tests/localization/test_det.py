"""Step 9: determinism (DET-01..05, 07, 08) and hostile-name adversarial rows that need whole recordings (ADV-10, 11b, 20, 21)."""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

from physicore_collector.localization import chains as H
from physicore_collector.localization import consistency as C
from physicore_collector.localization import output as O
from physicore_collector.localization.service import run_localization
from tests.localization import l2
from tests.localization.l1 import sym
from tests.localization.test_chn import pipe
from tests.localization.test_con import dep
from tests.localization.test_svc import DETS, cascade, deps

ROOT = Path(__file__).resolve().parents[2]


def cli_json(bag, cfg, d, *, env=None, cwd=None):
    e = {**os.environ, "PYTHONPATH": f"{ROOT}/src", **(env or {})}
    p = subprocess.run([sys.executable, "-m", "physicore_collector", "evidence", "localize", str(bag), "--anomaly-config", str(cfg), "--dependencies", str(d), "--json"],
                       capture_output=True, text=True, env=e, cwd=cwd or ROOT)
    assert p.returncode == 0, p.stderr
    return p.stdout


def test_DET_01_hash_seed_independent(tmp_path):
    bag, cfg = cascade(tmp_path)
    d = deps(tmp_path)
    outs = {cli_json(bag, cfg, d, env={"PYTHONHASHSEED": s}) for s in ("0", "1", "random")}
    assert len(outs) == 1


def test_DET_02_08_cwd_locale_and_timezone_independent(tmp_path):
    bag, cfg = cascade(tmp_path)
    d = deps(tmp_path)
    base = cli_json(bag, cfg, d)
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert cli_json(bag, cfg, d, cwd=other) == base
    assert cli_json(bag, cfg, d, env={"LC_ALL": "tr_TR.UTF-8", "LANG": "tr_TR.UTF-8", "TZ": "Pacific/Kiritimati"}) == base
    assert cli_json(bag, cfg, d, env={"LC_ALL": "C", "TZ": "America/St_Johns"}) == base


def test_DET_03_finding_order_does_not_matter():
    g = dep([("e1", "/a", "/b", 50), ("e2", "/b", "/c", 50), ("e3", "/a", "/c", None)])
    syms = [sym("a", 1, 1, topic="/a"), sym("b", 10, 10, topic="/b"), sym("c", 20, 20, topic="/c"), sym("d", 20, 40, topic="/c"), sym("e", 5, 5, topic="/zzz")]
    want = pipe(syms, g)[0].model_dump_json()
    rnd = random.Random(7)
    for _ in range(8):
        s2 = syms[:]
        rnd.shuffle(s2)
        assert pipe(s2, g)[0].model_dump_json() == want


def test_DET_04_edge_and_node_order_in_the_file_do_not_matter(tmp_path):
    bag, cfg = cascade(tmp_path)
    edges = [{"id": "e1", "from": "/rosout", "to": "n1", "relation": "data_flow", "window_ns": 10_000_000},
             {"id": "e2", "from": "n1", "to": "/diagnostics", "relation": "data_flow", "window_ns": 10_000_000},
             {"id": "e3", "from": "/rosout", "to": "/diagnostics", "relation": "dependency", "window_ns": 10_000_000}]
    nodes = [{"id": "n1"}, {"id": "n0"}]
    outs = []
    for i, (es, ns) in enumerate(((edges, nodes), (edges[::-1], nodes[::-1]), ([edges[1], edges[2], edges[0]], nodes))):
        p = tmp_path / f"d{i}.json"
        p.write_text(json.dumps({"dependency_schema_version": 1, "declared_by": "t", "nodes": ns, "edges": es}))
        r = run_localization(bag, cfg, dependencies=p)
        assert r.exit_code == 0
        j = json.loads(O.to_json(r))
        sha = j["inputs"]["dependencies"].pop("sha256")
        for l in j["links"]:
            for st in l["declared"]:
                st.pop("dependencies_sha256")
        outs.append((j, sha))
    assert outs[0][0] == outs[1][0] == outs[2][0] and outs[0][0]["inputs"]["dependencies"]["semantic_hash"]


def test_DET_05_same_layout_in_another_directory_gives_the_same_report(tmp_path):
    reports = []
    for name in ("one", "another_name"):
        t = tmp_path / name
        t.mkdir()
        bag, cfg = cascade(t)
        reports.append(O.to_json(run_localization(bag, cfg, dependencies=deps(t))))
    assert reports[0] == reports[1]


def test_DET_07_two_runs_in_one_process_are_identical(tmp_path):
    bag, cfg = cascade(tmp_path)
    d = deps(tmp_path)
    a = O.to_json(run_localization(bag, cfg, dependencies=d))
    run_localization(bag, cfg)
    assert O.to_json(run_localization(bag, cfg, dependencies=d)) == a


# ---------------------------------------------------------------------------------------------------------------------------------- ADV

HOSTILE = ["x" * 40 + "\x1b[31mred", "{0} %s ${x}", "root cause: culprit", "tab\there"]


def test_ADV_10_11b_hostile_names_in_the_recording_are_inert(tmp_path):
    topics = {"/rosout": (l2.LOG, [(l2.at(i + 1), l2.log(40, name)) for i, name in enumerate(HOSTILE)]), "/diagnostics": (l2.DIAG, [(l2.at(9), l2.diag(2, "motor"))])}
    bag = l2.build_bag(tmp_path, topics)
    cfg = l2.write_config(tmp_path, DETS)
    rep = run_localization(bag, cfg, dependencies=deps(tmp_path))
    assert rep.exit_code == 0
    text = O.render_text(rep)
    assert "\x1b" not in text and "\t" not in text
    assert [n for n, _ in O.render_lines(rep) if n == "UNMATCHED"] == []
    j = json.loads(O.to_json(rep))                      # valid JSON; hostile text only in data fields
    from physicore_collector.localization.report import find_forbidden_values
    from tests.localization.test_svc import DATA_KEYS
    assert find_forbidden_values(j, skip_keys=DATA_KEYS | frozenset({"logger_name", "diagnostic_name", "hardware_id", "goal_id", "battery_serial", "battery_location"})) == []
    for line in text.splitlines():
        if "root cause" in line.lower():
            assert '"' in line and line.lstrip().startswith(("Identity as reported by the source:", "Symptom ")), line


def test_ADV_20_expectation_on_a_topic_no_detector_covers_gives_m2_symptoms_and_m3c_scope_caveats(tmp_path):
    from tests import investigator_fixtures as F
    bag = F.write_mcap(tmp_path / "r.mcap", {"/temp": [(F.BASE + i * 100 * F.MS, {"v": v}) for i, v in enumerate([1, 2, 99])]})
    cfg = l2.write_config(tmp_path, {})                  # no M3c detector enabled
    exp = tmp_path / "exp.json"
    exp.write_text(json.dumps(F.contract([F.expectation("E1", check={"kind": "range", "max": 10})])))
    rep = run_localization(bag, cfg, expectations=exp)
    assert {s.source for s in rep.symptoms} == {"m2_finding"} and rep.symptoms
    assert {c.scope.detector for c in rep.caveats if c.code == "detector_not_configured"} >= {"tierA.ros_log", "tierB.publishing_gap"}


def test_ADV_21_two_segments_with_a_time_reset_keep_receipt_order_only(tmp_path):
    segs = [{"/rosout": [(l2.at(10), l2.log(40))]}, {"/diagnostics": [(l2.at(2), l2.diag(2))]}]
    bag = l2.build_bag(tmp_path, {"/rosout": (l2.LOG, []), "/diagnostics": (l2.DIAG, [])}, segments=segs)
    cfg = l2.write_config(tmp_path, DETS)
    rep = run_localization(bag, cfg, dependencies=deps(tmp_path))
    onset = {s.topic: s.onset.lower_ns for s in rep.symptoms}
    assert onset["/diagnostics"] < onset["/rosout"]            # receipt order is what the file says; no cross-segment re-ordering
    for l in rep.links:
        assert l.label == "inconsistent_order" and l.from_node == "/rosout"
    assert not any(l.label == "consistent_with_propagation" for l in rep.links)
    assert any(c.code == "receipt_order_not_occurrence_order" for c in rep.caveats)
