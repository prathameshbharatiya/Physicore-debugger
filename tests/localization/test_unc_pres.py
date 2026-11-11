"""UNC-01, UNC-02 (uncertainty copied / closed set) and PRES-06, PRES-11 (accepted deliverables untouched)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from physicore_collector.localization.symptoms import from_m3c
from physicore_collector.localization.vocabulary import M3D_UNCERTAINTY
from tests.localization import l2
from tests.localization.test_svc import cascade, deps

ROOT = Path(__file__).resolve().parents[2]
BASE = "b7bf26044b49"


def test_UNC_01_source_uncertainty_notes_are_copied_verbatim(tmp_path):
    topics = {"/t": ("std_msgs/msg/String", [(l2.at(i), {"data": "x"}) for i in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 30, 31)])}
    rep, _bag, _cfg = l2.m3c(tmp_path, topics, l2.GAP)
    assert rep.findings and any(f.uncertainty for f in rep.findings)
    by_id = {f.finding_id: f for f in rep.findings}
    syms = from_m3c(rep).symptoms
    assert len(syms) == len(rep.findings)
    for s in syms:
        f = by_id[s.source_id]
        assert [(n.code, n.params) for n in s.source_uncertainty] == [(n.code, n.params) for n in f.uncertainty]


def test_UNC_02_m3d_adds_only_its_four_codes(tmp_path):
    assert set(M3D_UNCERTAINTY) == {"onset_interval_width", "declared_edge_unverified", "path_via_unobserved_nodes", "blocked_defence"}
    from physicore_collector.localization.service import run_localization
    topics = {"/t": ("std_msgs/msg/String", [(l2.at(i), {"data": "x"}) for i in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 30, 31)])}
    bag = l2.build_bag(tmp_path, topics)
    cfg = l2.write_config(tmp_path, l2.GAP)
    rep = run_localization(bag, cfg, dependencies=deps(tmp_path, edges=[{"id": "e1", "from": "/t", "to": "/u", "relation": "data_flow"}]))
    used = {n for s in rep.symptoms for n in s.notes} | {n for l in rep.links for n in l.notes}
    assert used and used <= set(M3D_UNCERTAINTY)


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def test_PRES_06_accepted_examples_and_docs_are_byte_identical():
    r = _git("diff", "--name-status", BASE, "HEAD", "--", "examples", "docs")
    if r.returncode != 0:
        import pytest
        pytest.skip("git history of the M3c baseline is not available")
    changed = [l for l in r.stdout.splitlines() if l]
    changed = [l for l in changed if not ("examples/rootcause/" in l or "docs/M4_" in l)]  # M4
    assert all(l.startswith("A\t") and ("examples/localization/" in l or "docs/M3D_" in l or "examples/investigation_view/" in l or "docs/M3E_" in l) for l in changed), changed


def test_PRES_11_m3c_verifier_and_mutation_harness_are_unchanged():
    r = _git("diff", "--name-only", BASE, "HEAD", "--", "tests/anomaly", "tests/investigator_fixtures.py", "tests/conftest.py")
    if r.returncode != 0:
        import pytest
        pytest.skip("git history of the M3c baseline is not available")
    assert r.stdout.strip() in ("", "tests/anomaly/test_preservation.py")          # the one approved exemption (see test_preservation.py); nothing else
