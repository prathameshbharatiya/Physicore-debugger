"""PRES (M4-0): the accepted tree is untouched except the planned additive test edits; nothing accepted imports the M4 package."""
import ast
import difflib
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
G = Path(__file__).with_name("golden")
MANIFEST = G / "M4_BASE_FILES.sha256"
MANIFEST_SHA = "dd2ccdca2395b60674b033f8231b67fa00e2a3b85b300db3fd3fd3ea165e921f"
SRC = ROOT / "src" / "physicore_collector"
PKG = SRC / "rootcause"
NEW_PREFIXES = ("src/physicore_collector/rootcause/", "tests/rootcause/", "examples/rootcause/", "docs/M4_", "m4_records/")
# the planned, additive edits to accepted tests (M4-0): file -> baseline copy kept in m4_records
EDITED = {
    "tests/localization/test_preservation.py": "m3e_test_preservation.py.txt",
    "tests/localization/test_unc_pres.py": "m3e_test_unc_pres.py.txt",
    "tests/investigation_view/test_pres.py": "m3e_test_pres.py.txt",
}
CLI = "src/physicore_collector/cli.py"
EXPECTED_ADDED = {
    "tests/localization/test_preservation.py": 11,
    "tests/localization/test_unc_pres.py": 1,
}


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
    assert hashlib.sha256(MANIFEST.read_bytes()).hexdigest() == MANIFEST_SHA and len(manifest()) == 436


def test_PRES_02_every_accepted_file_is_byte_identical_except_the_planned_test_edits_and_the_one_cli_command():
    missing, changed = [], []
    for name, want in manifest().items():
        p = ROOT / name
        if not p.exists():
            missing.append(name)
        elif sha(p) != want and name not in EDITED and name != CLI:
            changed.append(name)
    assert not missing and not changed, (missing, changed)


def test_PRES_03_new_files_only_in_the_m4_paths():
    base = set(manifest())
    stray = [f for f in tracked_now() if f not in base and not f.startswith(NEW_PREFIXES)]
    assert not stray, stray


def _added_only(name, baseline):
    base = (ROOT / "m4_records" / baseline).read_text().splitlines()
    cur = (ROOT / name).read_text().splitlines()
    diff = [l for l in difflib.unified_diff(base, cur, lineterm="", n=0) if not l.startswith(("---", "+++", "@@"))]
    return diff


def test_PRES_04_the_planned_edits_of_accepted_tests_have_the_known_size_and_only_the_known_removals():
    for name, baseline in EDITED.items():
        diff = _added_only(name, baseline)
        removed = [l for l in diff if l.startswith("-")]
        added = [l for l in diff if l.startswith("+")]
        if name == "tests/investigation_view/test_pres.py":
            # the M3e preservation test: three lines are widened (the import rule, the pin tolerance, the CLI read that leaves out the M4 command), one is added
            assert len(removed) == 3 and len(added) == 4, diff
            assert any("if \"investigation_view\" in p.parts:" in l for l in removed) and any("diff = [l for l in difflib" in l for l in removed) and any("cur = (ROOT / CLI).read_text().splitlines()" in l for l in removed)
        else:
            assert not removed and len(added) == EXPECTED_ADDED[name], (name, diff)


HELP_BLOCK = [
    '+        for cmd in ("system-model", "hypotheses"):  # M4: the group help gains exactly the `system-model` and `hypotheses` rows too',
    '+            rows = [i for i, l in enumerate(keep) if l.startswith(f"\\u2502 {cmd} ")]',
    '+            assert rows, f"the evidence group help lists `{cmd}`"',
    '+            i = rows[0]',
    '+            j = i + 1',
    '+            while j < len(keep) and keep[j].startswith("\\u2502                  "):',
    '+                j += 1',
    '+            keep = keep[:i] + keep[j:]',
]


def test_PRES_05_every_added_line_in_the_accepted_tests_is_about_the_m4_paths_the_m4_package_or_the_help_rows_of_the_two_m4_commands():
    for name, baseline in EDITED.items():
        added = [l for l in _added_only(name, baseline) if l.startswith("+")]
        if name == "tests/localization/test_preservation.py":
            n = len(HELP_BLOCK)
            at = next(i for i in range(len(added) - n + 1) if added[i:i + n] == HELP_BLOCK)
            added = added[:at] + added[at + n:]
        for l in added:
            assert any(k in l for k in ("rootcause", "# M4", "continue", "examples/rootcause/", "docs/M4_", "m4_records")), (name, l)


def test_PRES_06_the_cli_diff_against_the_m3e_commit_is_two_added_commands_and_nothing_else():
    base = (ROOT / "m4_records" / "m3e_cli.py.txt").read_text().splitlines()
    cur = (ROOT / CLI).read_text().splitlines()
    diff = [l for l in difflib.unified_diff(base, cur, lineterm="", n=0) if not l.startswith(("---", "+++", "@@"))]
    if not diff:
        return                                       # before the milestone that adds the command
    assert all(l.startswith("+") for l in diff), [l for l in diff if not l.startswith("+")]
    text = "\n".join(diff)
    assert text.count("@evidence_app.command(") in (1, 2) and text.count('@evidence_app.command("system-model")') == 1
    assert text.count('@evidence_app.command("hypotheses")') == text.count("@evidence_app.command(") - 1
    assert text.count("@") == text.count("@evidence_app.command(")
    tree = ast.parse((ROOT / CLI).read_text())
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("rootcause" in (getattr(n, "module", "") or "") for n in top)       # imported lazily, inside the command


def _imports(path):
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append(("." * node.level) + (node.module or ""))
    return out


def test_PRES_07_no_accepted_package_imports_the_m4_package_except_the_lazy_cli_import():
    for p in SRC.rglob("*.py"):
        if "rootcause" in p.parts:
            continue
        for imp in _imports(p):
            if "rootcause" in imp.split("."):
                assert p.name == "cli.py", (p.name, imp)


def test_PRES_08_the_m4_package_imports_only_the_standard_library_pydantic_and_accepted_packages():
    std = set(sys.stdlib_module_names)
    allowed_pc = {"localization", "investigation_view", "investigator", "inventory", "rootcause"}
    for p in PKG.glob("*.py"):
        for imp in _imports(p):
            if imp.startswith("."):
                continue
            top = imp.split(".")[0]
            assert top in std or top in {"pydantic", "physicore_collector", "yaml"}, (p.name, imp)
            if top == "physicore_collector" and imp != "physicore_collector":
                assert imp.split(".")[1] in allowed_pc, (p.name, imp)


def test_PRES_09_no_dependency_change():
    assert sha(ROOT / "pyproject.toml") == manifest()["pyproject.toml"]


def test_PRES_10_the_m3d_and_m3e_example_outputs_are_unchanged():
    for name in ("examples/localization/expected_output.json", "examples/localization/expected_output.txt",
                 "examples/investigation_view/report.html", "examples/investigation_view/report.md"):
        assert sha(ROOT / name) == manifest()[name]


def test_PRES_11_the_m4_package_writes_nothing_and_uses_no_network_or_process():
    bad = ("write_text", "write_bytes", ".unlink(", "os.remove", "rmtree", "mkdir", "subprocess", "socket", "urllib", "requests", "http.client", "os.system", "shutil", "tempfile")
    for p in PKG.glob("*.py"):
        text = p.read_text()
        for b in bad:
            assert b not in text, (p.name, b)
