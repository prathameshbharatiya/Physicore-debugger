import json

import pytest

from physicore_collector.errors import MetadataError, TriggerError
from physicore_collector.inputs import load_metadata, load_trigger
from physicore_collector.schemas import TimeBase


def w(tmp_path, name, content):
    p = tmp_path / name
    p.write_bytes(content if isinstance(content, bytes) else content.encode())
    return p


GOOD = {"trigger_id": "t1", "timestamp_ns": 5, "timestamp_basis": "ros_time"}


def test_load_trigger_ok_and_keeps_exact_bytes(tmp_path):
    raw = json.dumps(GOOD, indent=4).encode()
    lt = load_trigger(w(tmp_path, "t.json", raw))
    assert lt.trigger.timestamp_basis is TimeBase.ROS_TIME and lt.raw == raw
    import hashlib
    assert lt.sha256 == hashlib.sha256(raw).hexdigest()


def test_trigger_with_attestation(tmp_path):
    d = dict(GOOD, basis_compatibility={"compatible_with_log_time": True, "established_by": "x"})
    assert load_trigger(w(tmp_path, "t.json", json.dumps(d))).trigger.basis_compatibility.established_by == "x"


def test_trigger_missing_timestamp_basis_is_rejected_with_field_name(tmp_path):
    d = {k: v for k, v in GOOD.items() if k != "timestamp_basis"}
    with pytest.raises(TriggerError, match="timestamp_basis: Field required"):
        load_trigger(w(tmp_path, "t.json", json.dumps(d)))


def test_trigger_unknown_field_and_bad_attestation_paths(tmp_path):
    d = dict(GOOD, extra=1, basis_compatibility={"compatible_with_log_time": True, "established_by": ""})
    with pytest.raises(TriggerError) as ei:
        load_trigger(w(tmp_path, "t.json", json.dumps(d)))
    assert "extra: unknown field" in str(ei.value) and "basis_compatibility.established_by" in str(ei.value)


@pytest.mark.parametrize("loader,err", [(load_trigger, TriggerError), (load_metadata, MetadataError)])
class TestCommon:
    def test_missing(self, tmp_path, loader, err):
        with pytest.raises(err, match="not found"):
            loader(tmp_path / "nope.json")

    def test_directory(self, tmp_path, loader, err):
        with pytest.raises(err, match="not a regular file"):
            loader(tmp_path)

    def test_malformed_json_has_position(self, tmp_path, loader, err):
        with pytest.raises(err, match=r"line 1, column"):
            loader(w(tmp_path, "x.json", "{oops"))

    def test_top_level_must_be_object(self, tmp_path, loader, err):
        with pytest.raises(err, match="JSON object at the top level"):
            loader(w(tmp_path, "x.json", "[1,2]"))

    def test_duplicate_keys(self, tmp_path, loader, err):
        with pytest.raises(err, match="duplicate key 'a'"):
            loader(w(tmp_path, "x.json", '{"a":1,"a":2}'))

    def test_non_utf8(self, tmp_path, loader, err):
        with pytest.raises(err, match="UTF-8"):
            loader(w(tmp_path, "x.json", b'{"a":"\xff"}'))

    def test_too_large(self, tmp_path, loader, err):
        with pytest.raises(err, match="larger than"):
            loader(w(tmp_path, "x.json", json.dumps(GOOD)), max_bytes=5)


def test_load_metadata(tmp_path):
    m = load_metadata(w(tmp_path, "m.json", '{"operator": "bob", "n": 3}'))
    assert m.data == {"operator": "bob", "n": 3}
