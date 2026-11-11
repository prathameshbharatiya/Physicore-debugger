"""EXH (M4e): the example hypothesis report is current, valid, and consistent across formats."""
import subprocess
import sys
from pathlib import Path

from physicore_collector.rootcause import investigate as V, view as W

ROOT = Path(__file__).resolve().parents[2]
EX = ROOT / "examples" / "rootcause"


def test_EXH_01_the_committed_example_files_are_current():
    rep = V.investigate(ROOT / "examples/localization/expected_output.json")
    assert (EX / "hypotheses_report.json").read_text() == V.to_json(rep)
    assert (EX / "hypotheses_report.md").read_text() == W.render_markdown(rep)
    assert (EX / "hypotheses_report.html").read_text() == W.render_html(rep)


def test_EXH_02_the_example_is_valid_and_passes_the_independent_parity_check():
    import json
    assert V.validate_report(json.loads((EX / "hypotheses_report.json").read_text())) == []
    r = subprocess.run([sys.executable, "-I", str(ROOT / "tests/rootcause/tools/parity_m4.py"), str(EX / "hypotheses_report.json"), str(EX / "hypotheses_report.md"),
                        str(EX / "hypotheses_report.html")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_EXH_03_the_example_says_what_was_not_supplied():
    import json
    d = json.loads((EX / "hypotheses_report.json").read_text())
    assert d["inputs"]["evidence"] == {"supplied": False} and d["inputs"]["model"] is None and d["verification"] is None
