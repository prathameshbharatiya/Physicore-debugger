"""CLI: the `evidence render` command (FR-CLI-*): exit codes and precedence, usage errors, read-only, byte-equality with the library."""
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from physicore_collector.cli import app
from physicore_collector.investigation_view.service import run_render

from .fx import SRC, EXAMPLE_BAG, EXAMPLE_JSON, ROOT, example_dict, gen, write_report

R = CliRunner()


def run(*args):
    return R.invoke(app, ["evidence", "render", *map(str, args)])


def tree(p: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p.rglob("*")):
        h.update(str(f.relative_to(p)).encode()); h.update(f.read_bytes() if f.is_file() else b"")
    return h.hexdigest()


@pytest.mark.parametrize("fmt", ["markdown", "html"])
def test_CLI_01_renders_the_example(fmt):
    r = run(EXAMPLE_JSON, "--format", fmt)
    assert r.exit_code == 0 and r.stdout_bytes.decode("utf-8") == run_render(EXAMPLE_JSON, fmt).text


def test_CLI_02_exit_1_when_verification_finds_a_mismatch(tmp_path):
    bag = tmp_path / "bag"; shutil.copytree(EXAMPLE_BAG, bag)
    b = bytearray((bag / "bag_0.mcap").read_bytes()); b[20] ^= 1; (bag / "bag_0.mcap").write_bytes(bytes(b))
    r = run(EXAMPLE_JSON, "--format", "markdown", "--verify-evidence", bag)
    assert r.exit_code == 1 and "mismatch" in r.stdout


def test_CLI_03_exit_0_when_verification_agrees():
    r = run(EXAMPLE_JSON, "--format", "html", "--verify-evidence", EXAMPLE_BAG)
    assert r.exit_code == 0 and "3 verified, 0 mismatch, 0 not checked" in r.stdout


def test_CLI_04_missing_report_is_exit_2():
    r = run("/no/such/report.json", "--format", "markdown")
    assert r.exit_code == 2 and "cannot read the report" in r.output


def test_CLI_05_invalid_report_is_exit_3_with_nothing_on_stdout(tmp_path):
    p = tmp_path / "bad.json"; p.write_text("{")
    r = run(p, "--format", "markdown")
    assert r.exit_code == 3 and "not valid JSON" in r.output


def test_CLI_06_unsupported_schema_is_exit_3(tmp_path):
    d = example_dict(); d["localization_schema_version"] = "9.9"
    assert run(write_report(tmp_path, d), "--format", "markdown").exit_code == 3


def test_CLI_07_directory_as_report_is_exit_2(tmp_path):
    assert run(tmp_path, "--format", "markdown").exit_code == 2


def test_CLI_08_format_is_required():
    assert run(EXAMPLE_JSON).exit_code == 2


@pytest.mark.parametrize("bad", ["xml", "HTML", "md", "", "json"])
def test_CLI_09_unknown_format_is_a_usage_error(bad):
    r = run(EXAMPLE_JSON, "--format", bad)
    assert r.exit_code == 2


def test_CLI_10_standard_input_is_refused():
    assert run("-", "--format", "markdown").exit_code == 2


def test_CLI_11_missing_verify_path_is_a_usage_error():
    assert run(EXAMPLE_JSON, "--format", "markdown", "--verify-evidence", "/no/such/dir").exit_code == 2


def test_CLI_12_there_is_no_output_option():
    assert run(EXAMPLE_JSON, "--format", "markdown", "--output", "x.md").exit_code == 2


def test_CLI_13_unknown_flag():
    assert run(EXAMPLE_JSON, "--format", "markdown", "--frobnicate").exit_code == 2


def test_CLI_14_exit_3_takes_precedence_over_verification(tmp_path):
    p = tmp_path / "bad.json"; p.write_text("[]")
    assert run(p, "--format", "markdown", "--verify-evidence", EXAMPLE_BAG).exit_code == 3


def test_CLI_15_nothing_is_written_anywhere(tmp_path):
    rep = write_report(tmp_path, gen(6, 1)); before = tree(tmp_path)
    run(rep, "--format", "html"); run(rep, "--format", "markdown")
    assert tree(tmp_path) == before


def test_CLI_16_subprocess_output_equals_the_library_in_any_locale(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(SRC), "LC_ALL": "C", "LANG": "C"}
    env.pop("PYTHONIOENCODING", None)
    for fmt in ("markdown", "html"):
        p = subprocess.run([sys.executable, "-m", "physicore_collector", "evidence", "render", str(EXAMPLE_JSON), "--format", fmt], capture_output=True, env=env, cwd=tmp_path)
        assert p.returncode == 0 and p.stdout == run_render(EXAMPLE_JSON, fmt).text.encode("utf-8")


def test_CLI_17_non_ascii_text_is_written_as_utf_8(tmp_path):
    d = gen(3, 1); d["symptoms"][0]["topic"] = "/té中"
    rep = write_report(tmp_path, d)
    env = {**os.environ, "PYTHONPATH": str(SRC), "LC_ALL": "C"}
    p = subprocess.run([sys.executable, "-m", "physicore_collector", "evidence", "render", str(rep), "--format", "markdown"], capture_output=True, env=env)
    assert p.returncode == 0 and "/té中".encode("utf-8") in p.stdout


def test_CLI_18_help_lists_the_options():
    out = R.invoke(app, ["evidence", "render", "--help"]).stdout
    for opt in ("--format", "--verify-evidence", "Exit codes"):
        assert opt in out
    assert "--output" not in out


def test_CLI_19_the_evidence_group_lists_render():
    assert "render" in R.invoke(app, ["evidence", "--help"]).stdout


def test_CLI_20_localize_and_render_compose(tmp_path):
    out = subprocess.run([sys.executable, "-m", "physicore_collector", "evidence", "localize", str(EXAMPLE_BAG), "--anomaly-config", str(ROOT / "examples" / "localization" / "anomaly.example.yaml"),
                          "--dependencies", str(ROOT / "examples" / "localization" / "dependencies.example.yaml"), "--json"], capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": str(SRC)})
    assert out.returncode in (0, 1), out.stderr
    rep = tmp_path / "r.json"; rep.write_text(out.stdout)
    r = run(rep, "--format", "markdown", "--verify-evidence", EXAMPLE_BAG)
    assert r.exit_code == 0 and "## Symptoms" in r.stdout


def test_CLI_21_lazy_import_the_view_package_is_not_loaded_by_other_commands():
    code = "import sys, physicore_collector.cli; print([m for m in sys.modules if 'investigation_view' in m])"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(SRC)}).stdout.strip()
    assert out == "[]"


def test_CLI_22_stderr_carries_problems_and_stdout_is_empty_on_exit_3(tmp_path):
    p = tmp_path / "bad.json"; p.write_text("{")
    r = subprocess.run([sys.executable, "-m", "physicore_collector", "evidence", "render", str(p), "--format", "html"], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(SRC)})
    assert r.returncode == 3 and r.stdout == "" and "not valid JSON" in r.stderr


def test_CLI_23_hostile_problem_text_is_cleaned_on_stderr(tmp_path):
    p = tmp_path / "bad.json"; p.write_bytes(b'{"localization_schema_version": "\\u202e\\u001b[31mX"}')
    r = subprocess.run([sys.executable, "-m", "physicore_collector", "evidence", "render", str(p), "--format", "html"], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(SRC)})
    assert r.returncode == 3 and "‮" not in r.stderr and "\x1b" not in r.stderr
