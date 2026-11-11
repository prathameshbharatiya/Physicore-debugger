"""CLIH (M4e): the `evidence hypotheses` command: formats, exit codes, refusals, read-only."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.rootcause.hyp_fx import scenario

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def sc(tmp_path_factory):
    t = tmp_path_factory.mktemp("cli")
    return {"F03": scenario(t, "F03"), "F02": scenario(t, "F02"), "tmp": t}


def cli(*args):
    env = {**os.environ, "PYTHONPATH": f"{ROOT / 'src'}", "COLUMNS": "100", "NO_COLOR": "1", "TERM": "dumb"}
    return subprocess.run([sys.executable, "-m", "physicore_collector", "evidence", "hypotheses", *map(str, args)], capture_output=True, text=True, env=env, cwd=ROOT)


def test_CLIH_01_json_is_the_default_and_valid(sc):
    r = cli(sc["F03"]["report"])
    assert r.returncode == 0 and r.stderr == ""
    d = json.loads(r.stdout)
    assert d["investigation_report_version"] == "1.0" and d["units"][0]["set_status"] == "leading"


@pytest.mark.parametrize("fmt,marker", [("markdown", "# Hypothesis report"), ("html", "<!doctype html>")])
def test_CLIH_02_markdown_and_html(sc, fmt, marker):
    r = cli(sc["F03"]["report"], "--format", fmt)
    assert r.returncode == 0 and r.stdout.startswith(marker)


def test_CLIH_03_options_are_used(sc):
    s = sc["F02"]
    r = cli(s["report"], "--dependencies", s["deps"], "--evidence", s["bag"], "--verify", "--baseline", sc["F03"]["report"])
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["verification"]["summary"]["mismatch"] == 0 and d["inputs"]["baseline"] is not None and d["problems"] == []


def test_CLIH_04_exit_codes(sc, tmp_path):
    assert cli(tmp_path / "missing.json").returncode == 2
    bad = tmp_path / "bad.json"
    bad.write_text("nope")
    r = cli(bad)
    assert r.returncode == 3 and r.stdout == "" and r.stderr
    assert cli(sc["F03"]["report"], "--format", "pdf").returncode == 2
    assert cli("-").returncode == 2
    assert cli(sc["F03"]["report"], "--evidence", tmp_path / "nope").returncode == 2
    assert cli(sc["F03"]["report"], "--verify").returncode == 2
    r = cli(sc["F03"]["report"], "--evidence", sc["F02"]["bag"], "--verify")
    assert r.returncode == 1 and json.loads(r.stdout)["exit_code"] == 1


def test_CLIH_05_the_command_writes_nothing(sc):
    before = sorted(p.name for p in sc["F03"]["dir"].rglob("*"))
    cli(sc["F03"]["report"], "--evidence", sc["F03"]["bag"], "--verify", "--format", "html")
    assert before == sorted(p.name for p in sc["F03"]["dir"].rglob("*"))


def test_CLIH_06_help_lists_the_options_and_the_exit_codes():
    r = cli("--help")
    assert r.returncode == 0
    for w in ("--model", "--dependencies", "--evidence", "--baseline", "--catalogue", "--verify", "--format", "Exit codes"):
        assert w in r.stdout
