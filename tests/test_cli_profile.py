import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from physicore_collector.cli import app
from tests.helpers import VALID

runner = CliRunner()


def run(*args):
    return runner.invoke(app, ["profile", "validate", *map(str, args)])


def test_valid_yaml_exit_0_and_summary(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(yaml.safe_dump(VALID))
    r = run(p)
    assert r.exit_code == 0, r.output
    assert "OK: profile 'test-arm' (schema 1.0) is valid" in r.output
    assert "topics:          3 (2 required)" in r.output
    assert "/joint_states [required] max gap 0.04 s" in r.output
    assert "/diag [optional] no cadence rule" in r.output
    assert "log_time_basis:  ros_time" in r.output


def test_valid_json_exit_0_and_prints_correct_hash(tmp_path):
    import hashlib
    p = tmp_path / "p.json"
    p.write_text(json.dumps(VALID))
    r = run(p)
    assert r.exit_code == 0
    assert hashlib.sha256(p.read_bytes()).hexdigest() in r.output


def test_unknown_time_basis_note_is_shown(tmp_path):
    d = dict(VALID, log_time_basis="unknown")
    p = tmp_path / "p.yaml"
    p.write_text(yaml.safe_dump(d))
    r = run(p)
    assert r.exit_code == 0 and "cannot be compared" in r.output


def test_missing_file_exit_2_with_error_prefix(tmp_path):
    r = run(tmp_path / "nope.yaml")
    assert r.exit_code == 2
    assert "ERROR: Profile file not found" in r.output and "Hint:" in r.output


def test_malformed_exit_2(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("a: [unclosed\n")
    r = run(p)
    assert r.exit_code == 2 and "not valid YAML" in r.output


def test_schema_violation_exit_2_lists_field_paths(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(yaml.safe_dump(dict(VALID, topics=[{"name": "/a", "expected_hz": 0, "bogus": 1}])))
    r = run(p)
    assert r.exit_code == 2
    assert "topics[0].expected_hz" in r.output and "topics[0].bogus: unknown field" in r.output


def test_errors_go_to_stderr_not_stdout(tmp_path):
    r = run(tmp_path / "x.yaml")
    assert r.stdout == ""
    assert "ERROR" in r.stderr


def test_missing_argument_is_usage_error_exit_2():
    r = runner.invoke(app, ["profile", "validate"])
    assert r.exit_code == 2


def test_shipped_example_validates_via_cli():
    example = Path(__file__).parent.parent / "examples/profiles/example_robot.profile.yaml"
    r = run(example)
    assert r.exit_code == 0 and "example-arm-v1" in r.output
