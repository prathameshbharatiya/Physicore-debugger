import hashlib
import json
from pathlib import Path

import pytest
import yaml

from physicore_collector import profile as profile_mod
from physicore_collector.errors import (
    InputError, ProfileNotFoundError, ProfileParseError, ProfileValidationError,
)
from physicore_collector.profile import load_profile
from physicore_collector.schemas import TimeBase
from tests.helpers import VALID



def write(tmp_path, name, content):
    p = tmp_path / name
    p.write_bytes(content if isinstance(content, bytes) else content.encode())
    return p


def write_yaml(tmp_path, data=None, name="p.yaml"):
    return write(tmp_path, name, yaml.safe_dump(VALID if data is None else data))


def mutated(**over):
    d = json.loads(json.dumps(VALID))
    d.update(over)
    return d


# ---------------- valid ----------------
def test_valid_yaml(tmp_path):
    lp = load_profile(write_yaml(tmp_path))
    assert lp.source_format == "yaml"
    assert lp.profile.profile_id == "test-arm"
    assert lp.profile.log_time_basis is TimeBase.ROS_TIME
    assert [t.effective_max_gap_seconds for t in lp.profile.topics] == [
        pytest.approx(0.04), 0.5, None]


@pytest.mark.parametrize("name", ["p.json", "P.JSON"])
def test_valid_json(tmp_path, name):
    lp = load_profile(write(tmp_path, name, json.dumps(VALID)))
    assert lp.source_format == "json"
    assert lp.profile.topics[1].name == "/camera"


def test_yml_extension_and_str_path(tmp_path):
    p = write_yaml(tmp_path, name="p.yml")
    assert load_profile(str(p)).path == p


def test_yaml_and_json_give_equal_profiles(tmp_path):
    a = load_profile(write_yaml(tmp_path)).profile
    b = load_profile(write(tmp_path, "p.json", json.dumps(VALID))).profile
    assert a == b


def test_utf8_bom_tolerated(tmp_path):
    p = write(tmp_path, "p.json", b"\xef\xbb\xbf" + json.dumps(VALID).encode())
    assert load_profile(p).profile.profile_id == "test-arm"


def test_shipped_example_profile_is_valid():
    example = Path(__file__).parent.parent / "examples/profiles/example_robot.profile.yaml"
    assert load_profile(example).profile.profile_id == "example-arm-v1"


def test_loading_is_deterministic(tmp_path):
    p = write_yaml(tmp_path)
    assert load_profile(p) == load_profile(p)


# ---------------- hash ----------------
def test_hash_is_of_raw_file_bytes(tmp_path):
    p = write_yaml(tmp_path)
    assert load_profile(p).sha256 == hashlib.sha256(p.read_bytes()).hexdigest()


def test_hash_changes_when_bytes_change_even_if_meaning_is_identical(tmp_path):
    p = write_yaml(tmp_path)
    h1 = load_profile(p).sha256
    p.write_bytes(p.read_bytes() + b"# harmless comment\n")
    lp2 = load_profile(p)
    assert lp2.sha256 != h1
    assert lp2.sha256 == hashlib.sha256(p.read_bytes()).hexdigest()


def test_hash_matches_parsed_bytes_single_pass(tmp_path, monkeypatch):
    """The digest must describe the bytes that were parsed, not a later re-read."""
    p = write_yaml(tmp_path)
    original = p.read_bytes()
    real = profile_mod.read_and_hash

    def read_then_tamper(path, **kw):
        out = real(path, **kw)
        Path(path).write_bytes(b"tampered: true\n")  # file changes after the read
        return out

    monkeypatch.setattr(profile_mod, "read_and_hash", read_then_tamper)
    lp = load_profile(p)
    assert lp.sha256 == hashlib.sha256(original).hexdigest()
    assert lp.profile.profile_id == "test-arm"


# ---------------- missing / unusable files ----------------
def test_missing_file(tmp_path):
    with pytest.raises(ProfileNotFoundError) as ei:
        load_profile(tmp_path / "nope.yaml")
    assert "nope.yaml" in str(ei.value) and ei.value.hint


def test_directory_is_not_a_profile(tmp_path):
    d = tmp_path / "dir.yaml"
    d.mkdir()
    with pytest.raises(ProfileNotFoundError, match="not a regular file"):
        load_profile(d)


def test_missing_file_reported_before_extension_problem(tmp_path):
    with pytest.raises(ProfileNotFoundError):
        load_profile(tmp_path / "nope.txt")


def test_unsupported_extension(tmp_path):
    p = write(tmp_path, "p.toml", "x=1")
    with pytest.raises(InputError, match="Unsupported profile file extension"):
        load_profile(p)


def test_oversized_file_rejected(tmp_path):
    p = write_yaml(tmp_path)
    with pytest.raises(InputError, match="larger than"):
        load_profile(p, max_bytes=10)


def test_empty_and_whitespace_files(tmp_path):
    for content in ["", "  \n\n"]:
        with pytest.raises(ProfileParseError, match="empty"):
            load_profile(write(tmp_path, "e.yaml", content))


def test_non_utf8_bytes(tmp_path):
    with pytest.raises(ProfileParseError, match="UTF-8"):
        load_profile(write(tmp_path, "b.yaml", b"profile_id: \xff\xfe\n"))


# ---------------- malformed syntax ----------------
def test_malformed_yaml_reports_line_and_column(tmp_path):
    p = write(tmp_path, "bad.yaml", "profile_id: x\ntopics: [unclosed\n")
    with pytest.raises(ProfileParseError, match=r"not valid YAML at line \d+, column \d+"):
        load_profile(p)


def test_yaml_tab_indentation(tmp_path):
    p = write(tmp_path, "tab.yaml", "topics:\n\t- name: /a\n")
    with pytest.raises(ProfileParseError, match="not valid YAML"):
        load_profile(p)


def test_malformed_json_reports_line_and_column(tmp_path):
    # exact position/wording of Python's json error differs between versions (3.13 changed it),
    # so assert the contract (a line/column is reported), not the specific numbers
    p = write(tmp_path, "bad.json", '{\n  "profile_id": "x",\n}\n')
    with pytest.raises(ProfileParseError, match=r"not valid JSON at line \d+, column \d+"):
        load_profile(p)


def test_yaml_with_python_object_tag_is_refused(tmp_path):
    p = write(tmp_path, "evil.yaml", "x: !!python/object/apply:os.system ['echo hi']\n")
    with pytest.raises(ProfileParseError, match="not valid YAML"):
        load_profile(p)


def test_multiple_yaml_documents_rejected(tmp_path):
    with pytest.raises(ProfileParseError, match="not valid YAML"):
        load_profile(write(tmp_path, "m.yaml", "a: 1\n---\nb: 2\n"))


@pytest.mark.parametrize("name,content", [("l.yaml", "- a\n- b\n"), ("s.yaml", "just text\n"),
                                          ("l.json", "[1, 2]"), ("n.json", "null")])
def test_top_level_must_be_mapping(tmp_path, name, content):
    with pytest.raises(ProfileParseError, match="mapping/object at the top level"):
        load_profile(write(tmp_path, name, content))


def test_duplicate_yaml_keys_rejected(tmp_path):
    p = write(tmp_path, "d.yaml", "profile_id: a\nprofile_id: b\ntopics: [{name: /a}]\n")
    with pytest.raises(ProfileParseError, match="duplicate key 'profile_id'"):
        load_profile(p)


def test_duplicate_nested_yaml_keys_rejected(tmp_path):
    p = write(tmp_path, "d.yaml", "topics:\n  - name: /a\n    name: /b\n")
    with pytest.raises(ProfileParseError, match="duplicate key 'name'"):
        load_profile(p)


def test_duplicate_json_keys_rejected(tmp_path):
    p = write(tmp_path, "d.json", '{"profile_id": "a", "profile_id": "b"}')
    with pytest.raises(ProfileParseError, match="duplicate key 'profile_id'"):
        load_profile(p)


# ---------------- schema violations ----------------
def issues_for(tmp_path, data):
    with pytest.raises(ProfileValidationError) as ei:
        load_profile(write_yaml(tmp_path, data))
    return ei.value


def test_missing_required_fields_named(tmp_path):
    e = issues_for(tmp_path, {"profile_schema_version": "1.0"})
    assert "profile_id: Field required" in e.issues
    assert "topics: Field required" in e.issues
    assert "failed validation (2 errors)" in str(e)


def test_unknown_top_level_field_is_named(tmp_path):
    e = issues_for(tmp_path, mutated(requried_metadata_keys=[]))
    assert any(i.startswith("requried_metadata_keys: unknown field") for i in e.issues)


def test_unknown_nested_field_has_indexed_path(tmp_path):
    d = mutated()
    d["topics"][1]["expected_hertz"] = 10
    e = issues_for(tmp_path, d)
    assert any(i.startswith("topics[1].expected_hertz: unknown field") for i in e.issues)


def test_invalid_nested_value_has_indexed_path(tmp_path):
    d = mutated()
    d["topics"][0]["expected_hz"] = -5
    e = issues_for(tmp_path, d)
    assert any(i.startswith("topics[0].expected_hz:") for i in e.issues)
    assert str(tmp_path) in str(e)  # message names the file


def test_unsupported_schema_version_message(tmp_path):
    e = issues_for(tmp_path, mutated(profile_schema_version="9.9"))
    assert any("profile_schema_version: unsupported profile_schema_version '9.9'" in i
               for i in e.issues)
    assert not any("Value error," in i for i in e.issues)


def test_unquoted_yaml_version_number_gets_actionable_hint(tmp_path):
    p = write(tmp_path, "p.yaml",
              "profile_schema_version: 1.0\nprofile_id: x\ntopics: [{name: /a}]\n")
    with pytest.raises(ProfileValidationError) as ei:
        load_profile(p)
    assert "Quote the version" in str(ei.value)


def test_duplicate_topics_reported_at_profile_level(tmp_path):
    d = mutated(topics=[{"name": "/a"}, {"name": "/a"}])
    e = issues_for(tmp_path, d)
    assert any(i.startswith("(profile): duplicate topic entries") for i in e.issues)


def test_empty_topics_list(tmp_path):
    e = issues_for(tmp_path, mutated(topics=[]))
    assert any(i.startswith("topics:") for i in e.issues)


def test_invalid_enum_value(tmp_path):
    e = issues_for(tmp_path, mutated(log_time_basis="banana"))
    assert any(i.startswith("log_time_basis:") for i in e.issues)


def test_topic_name_with_whitespace(tmp_path):
    d = mutated()
    d["topics"][0]["name"] = "/bad name"
    e = issues_for(tmp_path, d)
    assert any(i.startswith("topics[0].name:") and "whitespace" in i for i in e.issues)


@pytest.mark.parametrize("text", [".inf", ".nan", "-.inf"])
def test_non_finite_numbers_rejected_in_yaml(tmp_path, text):
    p = write(tmp_path, "p.yaml",
              f'profile_schema_version: "1.0"\nprofile_id: x\ntopics:\n  - {{name: /a, max_gap_seconds: {text}}}\n')
    with pytest.raises(ProfileValidationError) as ei:
        load_profile(p)
    assert any(i.startswith("topics[0].max_gap_seconds:") for i in ei.value.issues)


def test_non_finite_numbers_rejected_in_json(tmp_path):
    p = write(tmp_path, "p.json",
              '{"profile_schema_version":"1.0","profile_id":"x","topics":[{"name":"/a","expected_hz":Infinity}]}')
    with pytest.raises(ProfileValidationError):
        load_profile(p)


def test_many_errors_are_truncated_but_all_kept_in_issues(tmp_path):
    d = mutated(topics=[{"name": f"/t{i}", "expected_hz": -1} for i in range(30)])
    e = issues_for(tmp_path, d)
    assert len(e.issues) == 30
    assert "... and 10 more" in str(e)


def test_errors_chain_original_exception(tmp_path):
    with pytest.raises(ProfileValidationError) as ei:
        load_profile(write_yaml(tmp_path, {}))
    assert ei.value.__cause__ is not None
