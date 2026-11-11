"""CLIM (M4a): `evidence system-model`: exit codes, output, read-only behaviour, determinism."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.rootcause import mfx
from tests.rootcause.corpus import generate as G

import physicore_collector

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(physicore_collector.__file__).resolve().parents[1]      # the package under test (a mutated copy during mutation runs)


def cli(*args, seed="0", cwd=ROOT):
    env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": f"{SRC}:{ROOT}", "COLUMNS": "100", "NO_COLOR": "1", "TERM": "dumb", "LC_ALL": "C"}
    return subprocess.run([sys.executable, "-m", "physicore_collector", "evidence", "system-model", *map(str, args)], capture_output=True, text=True, env=env, cwd=cwd)


@pytest.fixture(scope="module")
def bag(tmp_path_factory):
    return G.generate("F01", 1, tmp_path_factory.mktemp("c")) / "bag"


def tree_hash(p):
    h = hashlib.sha256()
    for f in sorted(Path(p).rglob("*")):
        if f.is_file():
            h.update(f.name.encode() + f.read_bytes())
    return h.hexdigest()


def test_CLIM_01_a_valid_model_with_a_recording_prints_the_report_and_exits_zero(tmp_path, bag):
    m = mfx.write(tmp_path, mfx.mutated(lambda x: x.pop("dependency_file_sha256")), "m.yaml")
    r = cli(m, "--evidence", bag)
    assert r.returncode == 0 and r.stderr == ""
    assert "System model: test robot" in r.stdout and "Recording facts (from the recording" in r.stdout and "declared_topic_not_recorded: /cmd_mode" in r.stdout


def test_CLIM_02_json_output_parses_and_matches_the_text_facts(tmp_path, bag):
    m = mfx.write(tmp_path, mfx.valid())
    r = cli(m, "--evidence", bag, "--json")
    assert r.returncode == 0
    j = json.loads(r.stdout)
    assert j["model_report_version"] == "1.0" and j["model"]["source"] == "declared" and j["recording"]["source"] == "from_recording"
    assert {f["kind"] for f in j["findings"]} >= {"declared_topic_not_recorded", "dependency_file_not_supplied"}


def test_CLIM_03_no_recording_validates_only(tmp_path):
    r = cli(mfx.write(tmp_path, mfx.mutated(lambda x: x.pop("dependency_file_sha256"))))
    assert r.returncode == 0 and "Recording: not supplied" in r.stdout and "Findings: 0" in r.stdout


def test_CLIM_04_an_invalid_model_exits_three_with_every_problem_on_stderr_and_nothing_on_stdout(tmp_path, bag):
    m = mfx.write(tmp_path, mfx.mutated(lambda x: (x["components"][0].update(role="wizard"), x.update(declared_by=""))))
    r = cli(m, "--evidence", bag)
    assert r.returncode == 3 and r.stdout == "" and "$.components[0].role" in r.stderr and "$.declared_by" in r.stderr


def test_CLIM_05_missing_files_and_standard_input_are_usage_errors(tmp_path, bag):
    m = mfx.write(tmp_path, mfx.valid())
    r = cli(m, "--evidence", tmp_path / "absent")
    assert r.returncode == 2 and "the path does not exist" in r.stderr and r.stdout == ""
    assert cli(tmp_path / "absent.json").returncode == 3                                     # an unreadable model is an invalid input
    r = cli("-")
    assert r.returncode == 2 and "standard input is not accepted" in r.stderr
    assert cli(m, "--evidence", "-").returncode == 2
    assert cli(m, "--dependencies", "-").returncode == 2
    assert cli(m, "--dependencies", tmp_path / "absent").returncode == 2


def test_CLIM_06_the_dependency_digest_is_checked_against_the_file_supplied(tmp_path):
    dep = tmp_path / "dep.yaml"
    dep.write_bytes(b"dependency_schema_version: 1\n")
    good = mfx.write(tmp_path, mfx.mutated(lambda x: x.update(dependency_file_sha256=hashlib.sha256(dep.read_bytes()).hexdigest())), "good.json")
    bad = mfx.write(tmp_path, mfx.valid(), "bad.json")
    assert "Findings: 0" in cli(good, "--dependencies", dep).stdout
    out = cli(bad, "--dependencies", dep).stdout
    assert "dependency_file_digest_mismatch" in out


def test_CLIM_07_the_command_never_writes_into_the_evidence_or_the_model(tmp_path, bag):
    m = mfx.write(tmp_path, mfx.valid())
    before = (tree_hash(bag), hashlib.sha256(m.read_bytes()).hexdigest(), sorted(p.name for p in tmp_path.iterdir()))
    cli(m, "--evidence", bag)
    cli(m, "--evidence", bag, "--json")
    assert (tree_hash(bag), hashlib.sha256(m.read_bytes()).hexdigest(), sorted(p.name for p in tmp_path.iterdir())) == before


def test_CLIM_08_the_output_is_identical_across_runs_hash_seeds_and_working_directories(tmp_path, bag):
    m = mfx.write(tmp_path, mfx.valid())
    outs = {cli(m, "--evidence", bag, "--json", seed=s, cwd=c).stdout for s in ("0", "5") for c in (ROOT, tmp_path)}
    assert len(outs) == 1 and next(iter(outs)).startswith("{")
    assert len({cli(m, "--evidence", bag, seed=s).stdout for s in ("0", "5")}) == 1


def test_CLIM_09_the_group_help_lists_the_new_command_and_the_command_help_states_the_exit_codes():
    env = {**os.environ, "PYTHONPATH": f"{SRC}:{ROOT}", "COLUMNS": "100", "NO_COLOR": "1", "TERM": "dumb"}
    g = subprocess.run([sys.executable, "-m", "physicore_collector", "evidence", "--help"], capture_output=True, text=True, env=env, cwd=ROOT).stdout
    assert "system-model" in g
    h = subprocess.run([sys.executable, "-m", "physicore_collector", "evidence", "system-model", "--help"], capture_output=True, text=True, env=env, cwd=ROOT).stdout
    assert "Exit codes" in h and "--evidence" in h and "--dependencies" in h and "--json" in h


def test_CLIM_10_a_truncated_recording_still_gives_a_report_with_exit_one(tmp_path):
    from tests.rootcause.corpus import generate as GG
    d = GG.generate("F01", 1, tmp_path / "c") / "bag"
    seg = next(d.glob("*.mcap"))
    seg.write_bytes(seg.read_bytes()[:-400])
    m = mfx.write(tmp_path, mfx.mutated(lambda x: x.pop("dependency_file_sha256")))
    r = cli(m, "--evidence", d)
    assert r.returncode == 1 and "System model: test robot" in r.stdout and r.stderr == ""
