"""PRES-06..09 (TEST_PLAN 14): the accepted tree is unchanged except for files this milestone is allowed to touch."""
import ast
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).with_name("baseline_files.sha256")

# IMPLEMENTATION_PLAN 2.3: the only accepted file that may differ is cli.py (one added command), and only from step 9 on.
ALLOWED_EDITS = {"./src/physicore_collector/cli.py"}


def _baseline() -> dict[str, str]:
    out = {}
    for line in BASELINE.read_text().splitlines():
        sha, _, name = line.partition("  ")
        out[name] = sha
    return out


def test_PRES_06_accepted_files_are_byte_identical_except_the_allowed_edits():
    changed, missing = [], []
    for name, sha in _baseline().items():
        p = ROOT / name
        if not p.exists():
            missing.append(name)
        elif hashlib.sha256(p.read_bytes()).hexdigest() != sha and name not in ALLOWED_EDITS:
            changed.append(name)
    assert not missing and not changed, (missing, changed)


def test_PRES_06_baseline_manifest_is_the_pinned_one():
    # sha256 of the manifest recorded in STEP0_REPORT.md
    assert hashlib.sha256(BASELINE.read_bytes()).hexdigest() == "e97e8e69571e0efbb00f4ce774fda08918f34aa3b57a53418d8041753aa82936"


def test_PRES_07_no_new_mandatory_dependency():
    import tomllib
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert meta["dependencies"] == ["typer>=0.24,<1", "pydantic>=2.6,<3", "mcap>=1.2.2,<2", "pyyaml>=6,<7"]
    assert set(meta["optional-dependencies"]) == {"dev", "ros2", "xcheck"}
    assert meta["optional-dependencies"]["ros2"] == ["mcap-ros2-support>=0.6.0,<0.7"]


def test_PRES_08_only_cdr_py_imports_the_third_party_decoder():
    offenders = []
    for p in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(p.read_text())
        for n in ast.walk(tree):
            mods = []
            if isinstance(n, ast.Import):
                mods = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods = [n.module]
            if any(m.startswith("mcap_ros2") for m in mods) and p.name != "cdr.py":
                offenders.append(str(p))
    text = [str(p) for p in (ROOT / "src").rglob("*.py") if "mcap_ros2" in p.read_text() and p.name != "cdr.py"]
    assert not offenders
    assert [t for t in text if "/anomaly/" in t] == []


def test_PRES_09_investigator_does_not_import_anomaly():
    code = ("import sys, physicore_collector.investigator, physicore_collector.cli;"
            "print([m for m in sys.modules if m.startswith('physicore_collector.anomaly')])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "[]"


def test_PRES_09_no_accepted_module_mentions_the_anomaly_package_before_step_9():
    for p in (ROOT / "src" / "physicore_collector").rglob("*.py"):
        if "/anomaly/" in str(p) or "/localization/" in str(p) or p.name == "cli.py":      # M3d (approved 2026-11-07): the new localization package builds on the anomaly package
            continue
        assert "anomaly" not in p.read_text().lower().replace("anomalous", ""), p
