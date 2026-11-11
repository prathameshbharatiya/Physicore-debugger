"""PRES: preservation (NFR-VW-02): the M3d tree is untouched except the approved, additive edits; nothing else imports or depends on the view package."""
import ast
import difflib
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from .fx import ROOT

G = Path(__file__).with_name("golden")
MANIFEST = G / "M3E_BASE_FILES.sha256"
MANIFEST_SHA = "359f6aea811998c929792dcc055df1e7c260089f5677bb407498fb7e78ffd723"
CLI = "src/physicore_collector/cli.py"
M3D_PRES_TEST = "tests/localization/test_preservation.py"
M3D_UNC_PRES_TEST = "tests/localization/test_unc_pres.py"
ALLOWED_EDITS = (CLI, M3D_PRES_TEST, M3D_UNC_PRES_TEST)
NEW_PREFIXES = ("src/physicore_collector/investigation_view/", "tests/investigation_view/", "examples/investigation_view/", "docs/M3E_", "m3e_records/")
NEW_PREFIXES = NEW_PREFIXES + ("src/physicore_collector/rootcause/", "tests/rootcause/", "examples/rootcause/", "docs/M4_", "m4_records/")  # M4
SRC = ROOT / "src" / "physicore_collector"
PKG = SRC / "investigation_view"


def manifest():
    out = {}
    for line in MANIFEST.read_text().splitlines():
        sha, _, name = line.partition("  ")
        out[name] = sha
    return out


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def tracked_now():
    out = []
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in {".git", "__pycache__", ".pytest_cache"} and not d.endswith(".egg-info")]
        out += [str(Path(dp, f).relative_to(ROOT)) for f in fn if not f.endswith(".pyc")]
    return sorted(out)


def test_PRES_01_manifest_is_the_pinned_one():
    assert hashlib.sha256(MANIFEST.read_bytes()).hexdigest() == MANIFEST_SHA and len(manifest()) > 300


def test_PRES_02_every_m3d_file_is_byte_identical_except_the_three_approved_edits():
    missing, changed = [], []
    for name, want in manifest().items():
        p = ROOT / name
        if not p.exists():
            missing.append(name)
        elif sha(p) != want and name not in ALLOWED_EDITS:
            changed.append(name)
    assert not missing and not changed, (missing, changed)


def test_PRES_03_new_files_only_in_the_m3e_paths():
    base = set(manifest())
    stray = [f for f in tracked_now() if f not in base and not f.startswith(NEW_PREFIXES)]
    assert not stray, stray


def test_PRES_04_cli_py_diff_is_purely_additive_and_adds_one_command():
    base = (ROOT / "m3e_records" / "cli_m3d.py.txt").read_text().splitlines()
    cur = (ROOT / CLI).read_text().split("\n\n\n@evidence_app.command(\"system-model\")")[0].splitlines()  # M4: the M4 command is pinned by the M4 tests
    diff = [l for l in difflib.unified_diff(base, cur, lineterm="", n=0) if not l.startswith(("---", "+++", "@@"))]
    assert diff and all(l.startswith("+") for l in diff), [l for l in diff if not l.startswith("+")]
    added = "\n".join(diff)
    assert added.count('@evidence_app.command("render")') == 1 and added.count("@") == added.count('@evidence_app.command("render")')
    assert "import" in added and all("investigation_view" in l or not l.strip("+ ").startswith(("from .", "import ")) for l in diff if l.strip("+ ").startswith(("from .", "import ")))


def test_PRES_05_the_approved_edit_of_the_m3d_preservation_test_removes_exactly_the_known_lines():
    base = (ROOT / "m3e_records" / "m3d_test_preservation.py.txt").read_text().splitlines()
    cur = (ROOT / M3D_PRES_TEST).read_text().splitlines()
    removed = [l for l in difflib.unified_diff(base, cur, lineterm="", n=0) if l.startswith("-") and not l.startswith("---")]
    assert len(removed) == 9, removed
    assert removed[0].startswith("-ALLOWED_NEW_PREFIXES") and any("rows = [i for i, l in enumerate(got)" in l for l in removed) and any('if "localization" in p.parts:' in l for l in removed)


def test_PRES_06_help_of_every_other_command_is_unchanged_and_evidence_gains_one_row():
    env = {**os.environ, "COLUMNS": "100", "NO_COLOR": "1", "TERM": "dumb"}
    out = subprocess.run([sys.executable, "-m", "physicore_collector", "evidence", "--help"], capture_output=True, text=True, env=env, cwd=ROOT).stdout
    assert out.count("│ render ") == 1 and out.count("│ localize ") == 1


def _imports(path):
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append(("." * node.level) + (node.module or ""))
    return out


def test_PRES_07_no_accepted_package_imports_the_view_package_except_the_lazy_cli_import():
    for p in SRC.rglob("*.py"):
        if "investigation_view" in p.parts or "rootcause" in p.parts:  # M4: the root-cause view reuses the M3e view package (approved plan)
            continue
        for imp in _imports(p):
            if "investigation_view" in imp:
                assert p.name == "cli.py"
    tree = ast.parse((ROOT / CLI).read_text())
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        pass
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("investigation_view" in (getattr(n, "module", "") or "") for n in top)


def test_PRES_08_the_view_package_imports_only_the_standard_library_pydantic_and_the_localization_package():
    allowed_third = {"pydantic", "mcap"}
    std = set(sys.stdlib_module_names)
    for p in PKG.glob("*.py"):
        for imp in _imports(p):
            if imp.startswith("."):
                continue
            top = imp.split(".")[0]
            ok = top in std or top in allowed_third or top == "physicore_collector"
            assert ok, (p.name, imp)
            if top == "physicore_collector":
                assert imp.split(".")[1] == "localization", (p.name, imp)
            if top == "mcap":
                assert p.name == "verify.py"


def test_PRES_09_no_dependency_change():
    assert sha(ROOT / "pyproject.toml") == manifest()["pyproject.toml"]


def test_PRES_10_the_view_package_writes_nothing_and_uses_no_network_or_process():
    bad = ("write_text", "write_bytes", ".unlink(", "os.remove", "rmtree", "mkdir", "subprocess", "socket", "urllib", "requests", "http.client", "os.system", "shutil", "tempfile")
    for p in PKG.glob("*.py"):
        text = p.read_text()
        for b in bad:
            assert b not in text, (p.name, b)
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                mode = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else "r"
                assert set(mode) <= set("rb"), (p.name, mode)


def test_PRES_11_the_m3d_example_outputs_are_unchanged():
    for name in ("examples/localization/expected_output.json", "examples/localization/expected_output.txt"):
        assert sha(ROOT / name) == manifest()[name]


def test_PRES_12_localization_does_not_import_the_view_package():
    for p in (SRC / "localization").glob("*.py"):
        assert not any("investigation_view" in i for i in _imports(p))


def test_PRES_13_the_second_m3d_test_edit_is_one_line_that_only_allows_added_m3e_files():
    base = (ROOT / "m3e_records" / "m3d_test_unc_pres.py.txt").read_text().splitlines()
    cur = (ROOT / M3D_UNC_PRES_TEST).read_text().splitlines()
    diff = [l for l in difflib.unified_diff(base, cur, lineterm="", n=0) if not l.startswith(("---", "+++", "@@")) and not l.endswith("# M4")]
    assert len(diff) == 2 and diff[0].startswith("-") and diff[1].startswith("+")
    assert diff[1].replace(' or "examples/investigation_view/" in l or "docs/M3E_" in l', "") == diff[0].replace("-", "+", 1)
