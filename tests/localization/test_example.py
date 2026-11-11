"""The shipped example (examples/localization) reproduces its recorded expected output byte for byte."""
from pathlib import Path

from typer.testing import CliRunner

from physicore_collector.cli import app

EX = Path(__file__).resolve().parents[2] / "examples" / "localization"


def _run(*extra):
    return CliRunner().invoke(app, ["evidence", "localize", str(EX / "example_bag"), "--anomaly-config", str(EX / "anomaly.example.yaml"),
                                    "--dependencies", str(EX / "dependencies.example.yaml"), *extra])


def test_example_readable_output_matches():
    r = _run()
    assert r.exit_code == 0 and r.output == (EX / "expected_output.txt").read_text()


def test_example_json_output_matches():
    r = _run("--json")
    assert r.exit_code == 0 and r.output == (EX / "expected_output.json").read_text()
