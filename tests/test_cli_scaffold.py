import pytest
from typer.testing import CliRunner

from physicore_collector.cli import app

runner = CliRunner()


def test_help_lists_commands():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    for word in ["profile", "session", "export"]:
        assert word in r.output


@pytest.mark.parametrize("args", [["profile", "--help"], ["session", "--help"],
                                  ["profile", "validate", "--help"], ["session", "inspect", "--help"],
                                  ["session", "validate", "--help"], ["export", "--help"]])
def test_subcommand_help_works(args):
    assert runner.invoke(app, args).exit_code == 0


def test_version_flag():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0 and "0.1.0" in r.output


def test_export_has_no_force_flag():
    r = runner.invoke(app, ["export", "r.mcap", "--profile", "p.yaml", "--out", "o", "--force"])
    assert r.exit_code == 2  # unknown option
