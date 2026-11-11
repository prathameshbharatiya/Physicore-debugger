"""EXM (M4a): the example, its recorded outputs and the guide."""
import os
import subprocess
import sys
from pathlib import Path

import physicore_collector
from physicore_collector.localization.vocabulary import find_forbidden
from physicore_collector.rootcause.model import load_system_model

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(physicore_collector.__file__).resolve().parents[1]
EX = ROOT / "examples" / "rootcause"
BAG = "examples/localization/example_bag"


def run(*extra):
    env = {**os.environ, "PYTHONPATH": f"{SRC}:{ROOT}", "COLUMNS": "100", "NO_COLOR": "1", "TERM": "dumb", "LC_ALL": "C"}
    return subprocess.run([sys.executable, "-m", "physicore_collector", "evidence", "system-model", "examples/rootcause/system_model.example.yaml", "--evidence", BAG, *extra],
                          capture_output=True, text=True, env=env, cwd=ROOT)


def test_EXM_01_the_example_model_is_valid():
    r = load_system_model(EX / "system_model.example.yaml")
    assert r.model is not None and r.problems == [] and r.model.content["system_name"] == "example mobile robot"


def test_EXM_02_the_recorded_text_output_is_what_the_command_prints():
    r = run()
    assert r.returncode == 0 and r.stdout == (EX / "system_model_report.txt").read_text()


def test_EXM_03_the_recorded_json_output_is_what_the_command_prints():
    r = run("--json")
    assert r.returncode == 0 and r.stdout == (EX / "system_model_report.json").read_text()


def test_EXM_04_the_example_findings_are_the_four_the_readme_names():
    import json
    j = json.loads((EX / "system_model_report.json").read_text())
    assert [(f["kind"], f["subject"]) for f in j["findings"]] == [("declared_topic_not_recorded", "/lidar/scan"), ("constraint_cannot_be_evaluated", "lidar_range"),
                                                                  ("constraint_cannot_be_evaluated", "lidar_rate"), ("intervention_outside_recording", "base_restart")]


def test_EXM_05_the_guide_and_the_outputs_use_no_forbidden_word():
    for p in (ROOT / "docs" / "M4_SYSTEM_MODEL.md", EX / "README.md", EX / "system_model_report.txt"):
        assert find_forbidden(p.read_text()) == [], (p.name, find_forbidden(p.read_text()))
