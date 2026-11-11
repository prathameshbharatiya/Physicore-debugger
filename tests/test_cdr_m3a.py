"""M3a: CDR decoding and typed signal access (physicore_collector.investigator.cdr / decoding, `evidence decode`).

Layers, so that each environment runs what it can:
  * no extra installed            -> availability / structured-status tests, using the committed example bytes
  * extra `ros2` (the decoder)    -> decoding tests on the committed example, and on recordings built with the serializer below
  * extra `xcheck` (rosbags)      -> builds new CDR recordings (an independent serializer) and cross-checks values with a second decoder
Everything is synthetic and offline; nothing here shows that a real robot's rosbag2 output decodes (no real recording was available)."""

import dataclasses
import json
import random
import shutil
import struct
import time
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from physicore_collector.cli import app
from physicore_collector.inventory import inspect_evidence
from physicore_collector.investigator import Status, investigate, investigation_json, load_contract, parse_contract
from physicore_collector.investigator import cdr
from physicore_collector.investigator.contract import signal_path_problem
from physicore_collector.investigator.decoding import decode_topic
from tests.investigator_fixtures import contract, expectation, series, write_mcap
from tests.test_inventory import forbid_writes, tree_state

runner = CliRunner()
ROOT = Path(__file__).parent.parent
EXAMPLE = ROOT / "examples" / "m3a"
PHASE2_EXAMPLE = ROOT / "examples" / "investigator"
HAVE_DECODER = cdr.decoder_info().available

needs_decoder = pytest.mark.skipif(not HAVE_DECODER, reason="optional extra 'ros2' (mcap-ros2-support) is not installed")


@pytest.fixture
def cf():
    """tests.cdr_fixtures, or skip when the independent serializer (extra `xcheck`) is missing."""
    pytest.importorskip("rosbags")
    from tests import cdr_fixtures
    return cdr_fixtures


def run(path, exps):
    return investigate(inspect_evidence(path), parse_contract(contract(exps)))


def rid(r, eid="E1"):
    return next(x for x in r.expectation_results if x.expectation_id == eid)


def one_leaf(view, path, i=0):
    return next(x for x in view.messages[i].leaves if x.path == path)


@pytest.fixture
def example(tmp_path):
    p = tmp_path / "cdr_example.mcap"
    shutil.copy(EXAMPLE / "cdr_example.mcap", p)
    return p


# ------------------------------------------------------------------ 1. the optional dependency is optional (runs in every environment)

def test_decoder_info_never_raises_and_says_why_it_is_missing(monkeypatch):
    def boom():
        raise ImportError("No module named 'mcap_ros2'")
    monkeypatch.setattr(cdr, "_load_decoder_module", boom)
    info = cdr.decoder_info()
    assert info.available is False and info.name == "mcap-ros2-support" and "mcap_ros2" in info.reason and info.version is None


def test_a_broken_install_is_also_reported_not_raised(monkeypatch):
    monkeypatch.setattr(cdr, "_load_decoder_module", lambda: (_ for _ in ()).throw(RuntimeError("incompatible dependency")))
    assert cdr.decoder_info().available is False


def test_without_the_decoder_a_cdr_topic_is_insufficient_evidence_with_install_advice(example, monkeypatch):
    monkeypatch.setattr(cdr, "_load_decoder_module", lambda: (_ for _ in ()).throw(ImportError("No module named 'mcap_ros2'")))
    r = run(example, [expectation(topic="/joint_states", signal="position.0", check={"kind": "range", "max": 1})])
    x = rid(r)
    assert r.status is Status.INSUFFICIENT_EVIDENCE and x.reason_code == "payload_decoder_unavailable"
    assert "pip install physicore-collector[ros2]" in x.reason and x.evaluated_observation_count == 0
    pd = r.evidence.payload_decoding
    assert pd.decoder.available is False and pd.topics[0].status == "decoder_unavailable" and pd.topics[0].messages_attempted == 0
    assert pd.topics[0].schema_name == "sensor_msgs/msg/JointState" and pd.topics[0].schema_encoding == "ros2msg"


def test_without_the_decoder_the_decode_command_exits_2_with_a_status_not_a_traceback(example, monkeypatch):
    monkeypatch.setattr(cdr, "_load_decoder_module", lambda: (_ for _ in ()).throw(ImportError("nope")))
    res = runner.invoke(app, ["evidence", "decode", str(example), "-t", "/joint_states"])
    assert res.exit_code == 2 and "decoder_unavailable" in res.output and "Traceback" not in res.output
    j = runner.invoke(app, ["evidence", "decode", str(example), "-t", "/joint_states", "--json"])
    d = json.loads(j.output)
    assert d["status"] == "decoder_unavailable" and d["decoder"]["available"] is False and d["messages"][0]["status"] == "decoder_unavailable"
    assert all(m["leaves"] == [] for m in d["messages"])  # no value is ever made up


def test_the_inventory_and_json_investigations_do_not_need_the_extra(example, tmp_path):
    inv = inspect_evidence(example)
    assert inv.inspection.state == "complete" and {t.topic for t in inv.topics} >= {"/joint_states", "/odom", "/battery"}
    p = write_mcap(tmp_path / "j.mcap", {"/temp": series([1, 99])})
    assert run(p, [expectation(check={"kind": "range", "max": 10})]).status is Status.DIVERGENCE_FOUND


# ------------------------------------------------------------------ 2. Phase 2 behaviour is preserved

def test_phase2_example_output_is_byte_identical():
    res = runner.invoke(app, ["evidence", "investigate", str(PHASE2_EXAMPLE / "example_recording.mcap"), "-e",
                              str(PHASE2_EXAMPLE / "expectations.json"), "--json"])
    assert res.exit_code == 1 and res.output == (PHASE2_EXAMPLE / "expected_output.json").read_text()


def test_json_investigations_carry_no_cdr_fields(tmp_path):
    p = write_mcap(tmp_path / "j.mcap", {"/temp": series([1, 99])})
    text = investigation_json(run(p, [expectation(check={"kind": "range", "max": 10})]))
    assert "payload_decoding" not in text and "ros_type" not in text


def test_the_signal_grammar_is_extended_without_loosening_the_old_rules():
    for ok in ["position.0", "a.b.c", "position[name=joint2]", "pose.pose.position.x", "transforms.0.header.frame_id", "x[k=a/b:c-d_1]"]:
        assert signal_path_problem(ok) is None, ok
    for bad in ["", "a[0]", "a.*", "a[", "a[k=]", "a[=v]", "a[k=v]x", "a..b", "a b", "a[k=v w]", "a[k=v.w]", "a[k=v][j=w]", "a/b"]:
        assert signal_path_problem(bad) is not None, bad


# ------------------------------------------------------------------ 3. the committed example

@needs_decoder
def test_committed_example_decodes_to_the_committed_expected_values():
    expected = json.loads((EXAMPLE / "expected_decoded.json").read_text())
    inv = inspect_evidence(EXAMPLE / "cdr_example.mcap")
    import hashlib
    assert hashlib.sha256((EXAMPLE / "cdr_example.mcap").read_bytes()).hexdigest() == expected["recording_sha256"]
    for topic, exp in expected["topics"].items():
        v = decode_topic(inv, topic, limit=100)
        got = {"schema_name": v.schema_name, "schema_encoding": v.schema_encoding, "schema_sha256": v.schema_sha256,
               "message_encoding": v.message_encoding, "status": v.status, "messages": [m.model_dump(mode="json") for m in v.messages]}
        assert got == exp, topic


@needs_decoder
def test_example_spot_values_by_hand():
    """The expectations above are generated; these are written by hand from the generator's constants."""
    inv = inspect_evidence(EXAMPLE / "cdr_example.mcap")
    js = decode_topic(inv, "/joint_states", limit=5)
    assert [m.log_time_ns for m in js.messages] == [1_700_000_000_000_000_000 + i * 100_000_000 for i in range(5)]
    assert one_leaf(js, "name.1").value == "elbow" and one_leaf(js, "position.1", 3).value == "1.5"
    assert one_leaf(js, "header.frame_id").value == "base" and one_leaf(js, "effort.0").value == "1.5"
    assert one_leaf(js, "position.1", 3).ros_type == "float64" and one_leaf(js, "name.0").ros_type == "string"
    od = decode_topic(inv, "/odom")
    assert one_leaf(od, "pose.pose.position.x", 2).value == "1.0" and one_leaf(od, "twist.twist.angular.z").value == "0.125"
    assert one_leaf(od, "pose.covariance.15").value == "15.0" and od.messages[0].truncated_arrays[0] == "pose.covariance (36 elements)"  # views cap long arrays; signals still reach every element
    tw = decode_topic(inv, "/cmd_vel")
    assert one_leaf(tw, "linear.x").value == "0.5" and one_leaf(tw, "angular.z", 2).value == "0.25"
    assert one_leaf(decode_topic(inv, "/status/mode"), "data", 1).value == "RUNNING"
    cnt = decode_topic(inv, "/status/count")
    assert one_leaf(cnt, "data", 0).value == "-7" and one_leaf(cnt, "data", 1).value == "2147483647" and one_leaf(cnt, "data").ros_type == "int32"
    es = decode_topic(inv, "/status/estop")
    assert (one_leaf(es, "data", 0).value, one_leaf(es, "data", 1).value) == (False, True) and one_leaf(es, "data").value_type == "boolean"
    temp = decode_topic(inv, "/temperature")
    assert one_leaf(temp, "data", 1).value == "0.30000000000000004"  # float64: the shortest decimal that reads back as that exact double
    bat = decode_topic(inv, "/battery")
    assert one_leaf(bat, "voltage").value == "12.3" and one_leaf(bat, "voltage").ros_type == "float32"  # float32 keeps its own type's text
    assert one_leaf(bat, "temperature").quality == "non_finite" and one_leaf(bat, "temperature").value is None
    assert one_leaf(bat, "present", 1).value is False and one_leaf(bat, "serial_number").value == "SN-7"


def test_committed_example_is_what_the_generator_writes(tmp_path, cf):
    from tests.make_cdr_example import write_example
    write_example(tmp_path / "gen") if HAVE_DECODER else None
    if not HAVE_DECODER:
        pytest.skip("regenerating expected_decoded.json needs the decoder too")
    for name in ("cdr_example.mcap", "expectations.json", "expected_decoded.json"):
        assert (tmp_path / "gen" / name).read_bytes() == (EXAMPLE / name).read_bytes(), f"examples/m3a/{name} is stale: run python -m tests.make_cdr_example"


@needs_decoder
def test_investigating_the_example_with_typed_signals(example):
    exps = json.loads((EXAMPLE / "expectations.json").read_text())["expectations"]
    r = investigate(inspect_evidence(example), parse_contract(json.loads((EXAMPLE / "expectations.json").read_text())))
    assert r.status is Status.DIVERGENCE_FOUND and r.first_divergence.expectation_id == "ELBOW-LIMIT"
    f = r.first_divergence
    assert f.discrepancy == "1.5 is above the maximum 1.0 by 0.5" and f.time.ns == 1_700_000_000_300_000_000
    assert f.observations[0].signal == "position[name=elbow]" and f.observations[0].ros_type == "float64"
    by = {x.expectation_id: x for x in r.expectation_results}
    assert by["BATTERY-NOT-LOW"].status is Status.NO_DIVERGENCE and len(exps) == 2  # float32 12.3 / 12.1 are >= 12.0
    pd = r.evidence.payload_decoding
    assert {t.topic for t in pd.topics} == {"/joint_states", "/battery"} and all(t.status == "ok" for t in pd.topics)
    assert next(t for t in pd.topics if t.topic == "/joint_states").messages_decoded == 5


@needs_decoder
def test_cli_investigate_on_the_example(example):
    res = runner.invoke(app, ["evidence", "investigate", str(example), "-e", str(EXAMPLE / "expectations.json")])
    assert res.exit_code == 1 and "ELBOW-LIMIT" in res.output
    res = runner.invoke(app, ["evidence", "decode", str(example), "-t", "/odom", "-n", "1"])
    assert res.exit_code == 0 and "pose.pose.position.x = '0.0'" in res.output and "(number, float64)" in res.output


# ------------------------------------------------------------------ 4. independent decoder cross-check

def _rb_leaves(obj, prefix, out):
    import numpy as np
    if dataclasses.is_dataclass(obj):
        for f in dataclasses.fields(obj):
            if not f.name.startswith("__") and not f.name.isupper():  # rosbags' type tags and message constants
                _rb_leaves(getattr(obj, f.name), f"{prefix}.{f.name}" if prefix else f.name, out)
    elif isinstance(obj, (list, tuple, np.ndarray)):
        for i, v in enumerate(obj):
            _rb_leaves(v, f"{prefix}.{i}", out)
    else:
        out[prefix] = obj


@needs_decoder
def test_every_decoded_value_agrees_with_a_second_independent_decoder(cf):
    """Both decoders read the SAME bytes of the committed example: ours (mcap-ros2-support, wrapped) and `rosbags`' own."""
    import numpy as np
    from mcap.reader import make_reader
    ts = cf.typestore()
    inv = inspect_evidence(EXAMPLE / "cdr_example.mcap")
    with open(EXAMPLE / "cdr_example.mcap", "rb") as fh:
        r = make_reader(fh)
        summary = r.get_summary()
        chans = summary.channels
        raws = {}
        for schema, channel, message in r.iter_messages():
            raws.setdefault(channel.topic, []).append((message.log_time, schema.name, message.data))
    compared = 0
    for topic, rows in raws.items():
        view = decode_topic(inv, topic, limit=100)
        assert len(view.messages) == len(rows)
        for dm, (log_time, msgtype, data) in zip(view.messages, sorted(rows, key=lambda x: x[0])):
            assert dm.log_time_ns == log_time
            theirs = {}
            _rb_leaves(ts.deserialize_cdr(data, msgtype), "", theirs)
            for leaf in dm.leaves:
                want = theirs[leaf.path]
                if isinstance(want, (float, np.floating)):
                    if not np.isfinite(want):
                        assert leaf.quality == "non_finite" and leaf.value is None
                    else:
                        txt = cdr.float32_text(float(want)) if leaf.ros_type == "float32" else repr(float(want))
                        assert Decimal(leaf.value) == Decimal(txt), (topic, leaf.path)
                        if leaf.ros_type == "float32":
                            assert np.float32(float(leaf.value)) == np.float32(want)
                elif isinstance(want, (bool, np.bool_)):
                    assert leaf.value is bool(want) and leaf.value_type == "boolean"
                elif isinstance(want, (int, np.integer)):
                    assert Decimal(leaf.value) == Decimal(int(want)) and leaf.value_type == "number"
                else:
                    assert leaf.value == want and leaf.value_type == "string"
                compared += 1
            assert len(dm.leaves) + sum(int(t.split("(")[1].split()[0]) - cdr_cap() for t in dm.truncated_arrays) == len(theirs)
    assert compared > 150 and len(chans) == 8


def cdr_cap():
    from physicore_collector.investigator.decoding import MAX_ARRAY_LEAVES
    return MAX_ARRAY_LEAVES


# ------------------------------------------------------------------ 5. typed values from freshly serialized messages

@needs_decoder
@pytest.mark.parametrize("store_name", ["ROS2_HUMBLE", "ROS2_JAZZY", "ROS2_KILTED", "ROS2_LYRICAL"])
def test_common_ros2_scalar_schema_decodes_across_distribution_typestores(tmp_path, cf, store_name):
    """The same standard signal remains readable from embedded definitions across ROS 2 distro generations."""
    ts = cf.typestore(store_name)
    msgtype = "std_msgs/msg/Float64"
    message = ts.types[msgtype](data=12.5)
    schema = ts.generate_msgdef(msgtype, ros_version=2)[0].encode()
    payload = bytes(ts.serialize_cdr(message, msgtype))
    path = cf.write_cdr_mcap(
        tmp_path / f"{store_name}.mcap",
        [("/test/value", msgtype, [(cf.t(0), payload)])],
        schema_override={msgtype: schema},
    )
    leaf = one_leaf(decode_topic(inspect_evidence(path), "/test/value"), "data")
    assert (leaf.value, leaf.ros_type, leaf.quality) == ("12.5", "float64", "ok")


@needs_decoder
@pytest.mark.parametrize("typename, build, signal, expected, ros_type", [
    ("std_msgs/msg/Int64", lambda cf, ts: ts.types["std_msgs/msg/Int64"](data=9223372036854775807), "data", "9223372036854775807", "int64"),
    ("std_msgs/msg/UInt64", lambda cf, ts: ts.types["std_msgs/msg/UInt64"](data=18446744073709551615), "data", "18446744073709551615", "uint64"),
    ("std_msgs/msg/Float32", lambda cf, ts: ts.types["std_msgs/msg/Float32"](data=0.1), "data", "0.1", "float32"),
    ("std_msgs/msg/Float64", lambda cf, ts: cf.float64(0.1), "data", "0.1", "float64"),
    ("std_msgs/msg/Float64", lambda cf, ts: cf.float64(1e-7), "data", "1E-7", "float64"),
    ("std_msgs/msg/String", lambda cf, ts: cf.string("héllo ✓"), "data", "héllo ✓", "string"),
    ("std_msgs/msg/Bool", lambda cf, ts: cf.boolean(True), "data", True, "bool"),
    ("std_msgs/msg/Int32", lambda cf, ts: cf.int32(-2147483648), "data", "-2147483648", "int32"),
    ("geometry_msgs/msg/Twist", lambda cf, ts: cf.twist((1.25, -0.5, 0.0), (0.0, 0.0, 0.75)), "angular.z", "0.75", "float64"),
])
def test_scalar_and_nested_values_are_exact_and_typed(tmp_path, cf, typename, build, signal, expected, ros_type):
    ts = cf.typestore()
    p = cf.write_cdr_mcap(tmp_path / "r.mcap", [("/x", typename, [(cf.t(0), build(cf, ts))])])
    leaf = one_leaf(decode_topic(inspect_evidence(p), "/x"), signal)
    assert (leaf.value, leaf.ros_type, leaf.quality) == (expected, ros_type, "ok")


@needs_decoder
def test_float32_is_read_as_its_own_type_so_a_decimal_limit_behaves_as_written(tmp_path, cf):
    ts = cf.typestore()
    msgs = [(cf.t(i), ts.types["std_msgs/msg/Float32"](data=v)) for i, v in enumerate([0.1, 12.3, 0.3])]
    p = cf.write_cdr_mcap(tmp_path / "r.mcap", [("/x", "std_msgs/msg/Float32", msgs)])
    inv = inspect_evidence(p)
    assert [leaf.value for leaf in (one_leaf(decode_topic(inv, "/x"), "data", i) for i in range(3))] == ["0.1", "12.3", "0.3"]
    ok = investigate(inv, parse_contract(contract([expectation(topic="/x", signal="data", check={"kind": "range", "max": 12.3})])))
    assert ok.status is Status.NO_DIVERGENCE  # the stored float32 value IS the nearest float32 to 12.3, which reads as 12.3
    bad = investigate(inv, parse_contract(contract([expectation(topic="/x", signal="data", check={"kind": "range", "max": 12.2})])))
    assert bad.first_divergence.discrepancy == "12.3 is above the maximum 12.2 by 0.1"


@needs_decoder
def test_non_finite_floats_are_flagged_never_converted(tmp_path, cf):
    msgs = [(cf.t(0), cf.float64(1.0)), (cf.t(1), cf.float64(float("nan"))), (cf.t(2), cf.float64(float("inf"))), (cf.t(3), cf.float64(99.0))]
    p = cf.write_cdr_mcap(tmp_path / "r.mcap", [("/x", "std_msgs/msg/Float64", msgs)])
    v = decode_topic(inspect_evidence(p), "/x")
    assert [(m.leaves[0].quality, m.leaves[0].value) for m in v.messages[1:3]] == [("non_finite", None)] * 2
    r = run(p, [expectation(topic="/x", signal="data", check={"kind": "range", "max": 10})])
    x = rid(r)
    assert r.status is Status.INSUFFICIENT_EVIDENCE and r.status_reason_code == "earliest_divergence_not_defensible"
    assert {b.code for b in x.blind_spots} == {"non_finite"}  # the NaN/Inf samples may have been earlier mismatches: no earliest claim


# ------------------------------------------------------------------ 6. signal access: parallel-array selectors

@needs_decoder
def test_selector_picks_the_element_by_joint_name(tmp_path, cf):
    msgs = [(cf.t(i), cf.joint_state(cf.t(i), ["a", "b", "c"], [i, 10 + i, 20 + i])) for i in range(3)]
    p = cf.write_cdr_mcap(tmp_path / "r.mcap", [("/j", "sensor_msgs/msg/JointState", msgs)])
    r = run(p, [expectation(topic="/j", signal="position[name=b]", check={"kind": "range", "max": 11})])
    assert r.first_divergence.observations[0].value == Decimal("12") and r.first_divergence.time.ns == cf.t(2)
    assert r.first_divergence.observations[0].signal == "position[name=b]"
    assert run(p, [expectation(topic="/j", signal="position[name=c]", check={"kind": "range", "max": 100})]).status is Status.NO_DIVERGENCE


@needs_decoder
def test_selector_failures_are_blind_spots_not_guesses(tmp_path, cf):
    dup = [(cf.t(0), cf.joint_state(cf.t(0), ["a", "a"], [1.0, 2.0]))]
    p = cf.write_cdr_mcap(tmp_path / "dup.mcap", [("/j", "sensor_msgs/msg/JointState", dup)])
    r = run(p, [expectation(topic="/j", signal="position[name=a]")])
    assert {b.code for b in rid(r).blind_spots} == {"signal_ambiguous"} and r.status is Status.INSUFFICIENT_EVIDENCE
    p2 = cf.write_cdr_mcap(tmp_path / "unk.mcap", [("/j", "sensor_msgs/msg/JointState", [(cf.t(0), cf.joint_state(cf.t(0), ["a"], [1.0]))])])
    assert {b.code for b in rid(run(p2, [expectation(topic="/j", signal="position[name=zzz]")])).blind_spots} == {"signal_missing"}
    short = [(cf.t(0), cf.joint_state(cf.t(0), ["a", "b"], [1.0]))]  # names longer than positions
    p3 = cf.write_cdr_mcap(tmp_path / "short.mcap", [("/j", "sensor_msgs/msg/JointState", short)])
    assert {b.code for b in rid(run(p3, [expectation(topic="/j", signal="position[name=b]")])).blind_spots} == {"signal_missing"}
    assert run(p3, [expectation(topic="/j", signal="position[name=a]", check={"kind": "range", "max": 5})]).status is Status.NO_DIVERGENCE


def test_selector_also_works_on_json_messages(tmp_path):
    msgs = [(1_700_000_000_000_000_000, {"name": ["x", "y"], "position": [1, 50]})]
    p = write_mcap(tmp_path / "j.mcap", {"/j": msgs})
    r = run(p, [expectation(topic="/j", signal="position[name=y]", check={"kind": "range", "max": 10})])
    assert r.status is Status.DIVERGENCE_FOUND and r.first_divergence.observations[0].ros_type is None


@needs_decoder
def test_a_signal_that_is_not_a_leaf_or_not_in_the_type_is_not_a_value(tmp_path, cf):
    p = cf.write_cdr_mcap(tmp_path / "r.mcap", [("/o", "nav_msgs/msg/Odometry", [(cf.t(0), cf.odometry(cf.t(0), 1.0, 2.0, 0.5, 0.1))])])
    assert {b.code for b in rid(run(p, [expectation(topic="/o", signal="pose.pose")])).blind_spots} == {"type_unsupported"}
    assert {b.code for b in rid(run(p, [expectation(topic="/o", signal="pose.pose.position.q")])).blind_spots} == {"signal_missing"}
    assert {b.code for b in rid(run(p, [expectation(topic="/o", signal="pose.covariance.99")])).blind_spots} == {"signal_missing"}


# ------------------------------------------------------------------ 7. provenance is preserved

@needs_decoder
def test_findings_keep_artifact_digest_message_index_topic_time_and_clock_domain(tmp_path, cf):
    """Written as a real rosbag2 directory (metadata.yaml + MCAP) by the independent serializer."""
    from rosbags.rosbag2 import Writer
    from rosbags.rosbag2.enums import StoragePlugin
    ts = cf.typestore()
    bag = tmp_path / "bagc"
    with Writer(bag, version=9, storage_plugin=StoragePlugin.MCAP) as w:
        c1 = w.add_connection("/joint_states", "sensor_msgs/msg/JointState", typestore=ts)
        c2 = w.add_connection("/temperature", "std_msgs/msg/Float64", typestore=ts)
        for i in range(4):
            w.write(c2, cf.t(i, 50), ts.serialize_cdr(cf.float64(20.0 + i), "std_msgs/msg/Float64"))
            w.write(c1, cf.t(i), ts.serialize_cdr(cf.joint_state(cf.t(i), ["j"], [0.5 * i]), "sensor_msgs/msg/JointState"))
    mcap_file = next(bag.glob("*.mcap"))
    inv = inspect_evidence(bag)
    r = investigate(inv, parse_contract(contract([expectation(topic="/joint_states", signal="position[name=j]", check={"kind": "range", "max": 0.75})])))
    f = r.first_divergence
    import hashlib
    sha = hashlib.sha256(mcap_file.read_bytes()).hexdigest()
    assert (f.sources[0].relative_path, f.sources[0].artifact_sha256, f.sources[0].topic) == (mcap_file.name, sha, "/joint_states")
    assert f.time.ns == cf.t(2) and f.time.clock_domain == "mcap_log_time" and f.observations[0].clock_domain == "mcap_log_time"
    assert f.observations[0].timestamp_ns == cf.t(2) and f.observations[0].artifact_ref == f"artifact:{mcap_file.name}"
    # the message index is the position in the FILE (both topics interleave, so it is not 2): verify against a direct read of the file
    from mcap.reader import make_reader
    with open(mcap_file, "rb") as fh:
        order = [(ch.topic, m.log_time) for _s, ch, m in make_reader(fh).iter_messages(log_time_order=False)]
    assert order[f.observations[0].message_index] == ("/joint_states", cf.t(2))
    assert f.observations[0].observation_id == f"obs:{mcap_file.name}#{f.observations[0].message_index}"
    pd = r.evidence.payload_decoding
    with open(mcap_file, "rb") as fh:
        schema_bytes = next(s.data for s in make_reader(fh).get_summary().schemas.values() if s.name == "sensor_msgs/msg/JointState")
    assert pd.topics[0].schema_sha256 == hashlib.sha256(schema_bytes).hexdigest() and r.evidence.segments[0].sha256 == sha


@needs_decoder
def test_decode_view_and_investigation_agree_on_message_index(example):
    inv = inspect_evidence(example)
    v = decode_topic(inv, "/joint_states", limit=5)
    r = investigate(inv, load_contract_dict({"E1": ("/joint_states", "position[name=elbow]", {"kind": "range", "max": 1.0})}))
    f = r.first_divergence
    assert f.observations[0].message_index == v.messages[3].message_index and f.observations[0].observation_id == v.messages[3].observation_prefix


def load_contract_dict(spec):
    return parse_contract(contract([expectation(eid, topic=t, signal=s, check=c) for eid, (t, s, c) in spec.items()]))


@needs_decoder
def test_a_changed_cdr_file_is_not_trusted(example):
    inv = inspect_evidence(example)
    b = bytearray(example.read_bytes())
    b[len(b) // 3] ^= 0xFF
    example.write_bytes(bytes(b))
    r = investigate(inv, parse_contract(contract([expectation(topic="/joint_states", signal="position.0")])))
    assert r.status is Status.INSUFFICIENT_EVIDENCE and "no longer match the digest" in r.evidence.segments[0].reason


# ------------------------------------------------------------------ 8. failures are statuses, never fabricated values or exceptions

def _decode_status(path, topic="/x"):
    v = decode_topic(inspect_evidence(path), topic)
    return v


@needs_decoder
def test_missing_schema(tmp_path, cf):
    p = cf.write_cdr_mcap(tmp_path / "r.mcap", [("/x", "std_msgs/msg/Float64", [(cf.t(0), cf.float64(1.0))])], omit_schema=True)
    v = _decode_status(p)
    assert v.status == "schema_missing" and v.messages[0].leaves == []
    r = run(p, [expectation(topic="/x", signal="data")])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and rid(r).reason_code == "payload_schema_missing"


@needs_decoder
@pytest.mark.parametrize("schema_encoding", ["ros2idl", "jsonschema", "protobuf", ""])
def test_unsupported_schema_encodings(tmp_path, cf, schema_encoding):
    p = cf.write_cdr_mcap(tmp_path / "r.mcap", [("/x", "std_msgs/msg/Float64", [(cf.t(0), cf.float64(1.0))])], schema_encoding=schema_encoding)
    v = _decode_status(p)
    assert v.status == "schema_encoding_unsupported" and v.messages[0].leaves == []
    assert rid(run(p, [expectation(topic="/x", signal="data")])).reason_code == "payload_encoding_not_supported"


@needs_decoder
def test_unsupported_message_encoding(tmp_path, cf):
    p = cf.write_cdr_mcap(tmp_path / "r.mcap", [("/x", "std_msgs/msg/Float64", [(cf.t(0), cf.float64(1.0))])], message_encoding="protobuf")
    assert _decode_status(p).status == "message_encoding_unsupported"
    assert rid(run(p, [expectation(topic="/x", signal="data")])).reason_code == "payload_encoding_not_supported"


@needs_decoder
@pytest.mark.parametrize("text", [b"this is not a message definition", b"float64 data\nnot_a_type[ weird", b"\xff\xfe\x00", b"missing/msg/Type child\n"])
def test_invalid_schema_text_is_a_status(tmp_path, cf, text):
    p = cf.write_cdr_mcap(tmp_path / "r.mcap", [("/x", "std_msgs/msg/Float64", [(cf.t(0), cf.float64(1.0))])], schema_override={"std_msgs/msg/Float64": text})
    v = _decode_status(p)
    assert v.status in ("schema_invalid",) and v.messages[0].leaves == [] and v.messages[0].status == "schema_invalid"
    assert rid(run(p, [expectation(topic="/x", signal="data")])).reason_code == "payload_schema_invalid"


@needs_decoder
def test_every_truncation_of_a_message_is_malformed_never_a_value(tmp_path, cf):
    good = cf.cdr("sensor_msgs/msg/JointState", cf.joint_state(cf.t(0), ["joint_one", "j2"], [0.5, 1.5], [], [2.0]))
    p = cf.write_cdr_mcap(tmp_path / "r.mcap", [("/x", "sensor_msgs/msg/JointState", [(cf.t(0), good)])])
    info = cdr.SchemaInfo.of("sensor_msgs/msg/JointState", "ros2msg", cf.schema_text("sensor_msgs/msg/JointState"))
    codec = cdr.make_codec(info, "cdr")
    assert codec.decode(good).status == "ok"
    for n in range(len(good)):
        out = codec.decode(good[:n])
        assert out.status == "payload_malformed" and out.message is None, n
    assert codec.failures == {"payload_malformed": len(good)}


@needs_decoder
def test_malformed_messages_inside_a_recording_are_blind_spots(tmp_path, cf):
    good = [cf.joint_state(cf.t(i), ["j"], [float(i) * 3]) for i in range(5)]
    raw = [cf.cdr("sensor_msgs/msg/JointState", m) for m in good]
    msgs = [(cf.t(0), raw[0]), (cf.t(1), raw[1][:20]), (cf.t(2), raw[2]), (cf.t(3), b""), (cf.t(4), raw[4])]
    p = cf.write_cdr_mcap(tmp_path / "r.mcap", [("/x", "sensor_msgs/msg/JointState", msgs)])
    v = decode_topic(inspect_evidence(p), "/x", limit=10)
    assert [m.status for m in v.messages] == ["ok", "payload_malformed", "ok", "payload_malformed", "ok"] and v.status == "partial"
    assert v.messages[1].leaves == [] and v.messages[3].leaves == []
    r = run(p, [expectation(topic="/x", signal="position.0", check={"kind": "range", "max": 5})])
    x = rid(r)
    assert x.blind_spot_count == 2 and {b.code for b in x.blind_spots} == {"payload_malformed"}
    assert r.status is Status.INSUFFICIENT_EVIDENCE and r.status_reason_code == "earliest_divergence_not_defensible"  # a mismatch at +400ms, blind spot at +100ms
    pd = r.evidence.payload_decoding.topics[0]
    assert (pd.messages_attempted, pd.messages_decoded, pd.failures) == (5, 3, {"payload_malformed": 2})


@needs_decoder
def test_a_huge_declared_length_fails_fast(tmp_path, cf):
    good = bytearray(cf.cdr("std_msgs/msg/String", cf.string("hello")))
    good[4:8] = struct.pack("<I", 0xFFFFFFF0)  # string length claims ~4 GiB
    info = cdr.SchemaInfo.of("std_msgs/msg/String", "ros2msg", cf.schema_text("std_msgs/msg/String"))
    codec = cdr.make_codec(info, "cdr")
    t0 = time.time()
    out = codec.decode(bytes(good))
    assert time.time() - t0 < 2 and out.status in ("payload_malformed", "ok")
    arr = bytearray(cf.cdr("sensor_msgs/msg/JointState", cf.joint_state(cf.t(0), ["a"], [1.0])))
    info2 = cdr.SchemaInfo.of("sensor_msgs/msg/JointState", "ros2msg", cf.schema_text("sensor_msgs/msg/JointState"))
    c2 = cdr.make_codec(info2, "cdr")
    for off in range(0, len(arr) - 4, 4):  # blow up every 32-bit field in turn
        mutated = bytearray(arr)
        mutated[off:off + 4] = struct.pack("<I", 0xFFFFFFF0)
        t0 = time.time()
        assert c2.decode(bytes(mutated)).status in ("ok", "payload_malformed")
        assert time.time() - t0 < 2, off


@needs_decoder
def test_random_corruption_never_raises(tmp_path, cf):
    rng = random.Random(20260430)
    base = cf.cdr("nav_msgs/msg/Odometry", cf.odometry(cf.t(0), 1.0, 2.0, 0.5, 0.1))
    codec = cdr.make_codec(cdr.SchemaInfo.of("nav_msgs/msg/Odometry", "ros2msg", cf.schema_text("nav_msgs/msg/Odometry")), "cdr")
    statuses = set()
    for _ in range(400):
        b = bytearray(base)
        for _k in range(rng.randint(1, 4)):
            b[rng.randrange(len(b))] = rng.randrange(256)
        out = codec.decode(bytes(b))  # must return, whatever the bytes
        statuses.add(out.status)
        assert (out.message is None) == (out.status != "ok")
    assert statuses <= {"ok", "payload_malformed"}


@needs_decoder
def test_oversized_messages_are_refused_not_decoded(tmp_path, cf, monkeypatch):
    p = cf.write_cdr_mcap(tmp_path / "r.mcap", [("/x", "std_msgs/msg/Float64", [(cf.t(0), cf.float64(1.0)), (cf.t(1), cf.float64(99.0))])])
    monkeypatch.setattr(cdr, "MAX_CDR_MESSAGE_BYTES", 4)
    r = run(p, [expectation(topic="/x", signal="data", check={"kind": "range", "max": 10})])
    assert r.status is Status.INSUFFICIENT_EVIDENCE and {b.code for b in rid(r).blind_spots} == {"payload_undecodable"}
    assert r.evidence.payload_decoding.topics[0].failures == {"payload_too_large": 2}


@needs_decoder
def test_image_and_point_cloud_payloads_are_out_of_scope(tmp_path, cf):
    p = cf.write_cdr_mcap(tmp_path / "r.mcap", [("/cam", "sensor_msgs/msg/Image", [(cf.t(0), cf.image(cf.t(0)))])])
    v = _decode_status(p, "/cam")
    assert v.status == "payload_type_out_of_scope" and v.messages[0].leaves == []
    r = run(p, [expectation(topic="/cam", signal="height")])
    assert rid(r).reason_code == "payload_type_out_of_scope"
    assert r.evidence.payload_decoding.topics[0].messages_attempted == 0  # the decoder was never invoked


@needs_decoder
def test_wstring_is_reported_as_unsupported(tmp_path, cf):
    schema = b"wstring name\n"
    payload = b"\x00\x01\x00\x00" + struct.pack("<I", 2) + b"a\x00\x00\x00"
    p = cf.write_cdr_mcap(tmp_path / "r.mcap", [("/w", "pkg/msg/W", [(cf.t(0), payload)])], schema_override={"pkg/msg/W": schema})
    r = run(p, [expectation(topic="/w", signal="name")])
    assert {b.code for b in rid(r).blind_spots} == {"payload_unsupported_type"} and r.status is Status.INSUFFICIENT_EVIDENCE
    assert r.evidence.payload_decoding.topics[0].failures == {"payload_unsupported_type": 1}


@needs_decoder
def test_a_truncated_recording_file_with_cdr_payloads(tmp_path, cf):
    msgs = [(cf.t(i, 10), cf.float64(float(i))) for i in range(300)]
    full = cf.build_cdr_mcap([("/x", "std_msgs/msg/Float64", msgs)])
    p = tmp_path / "t.mcap"
    p.write_bytes(full[: int(len(full) * 0.6)])
    v = decode_topic(inspect_evidence(p), "/x", limit=3)
    assert v.status == "ok" and v.evidence_complete is False and len(v.messages) == 3
    r = runner.invoke(app, ["evidence", "decode", str(p), "-t", "/x", "-n", "2"])
    assert r.exit_code == 1  # decoded, but the evidence is incomplete
    inv_r = run(p, [expectation(topic="/x", signal="data", check={"kind": "range", "max": 1000})])
    assert inv_r.status is Status.INSUFFICIENT_EVIDENCE and rid(inv_r).reason_code == "evidence_incomplete"


def test_decode_of_unknown_topic_and_missing_input_are_statuses(tmp_path, example):
    v = decode_topic(inspect_evidence(example), "/nope")
    assert v.status == "topic_not_found"
    res = runner.invoke(app, ["evidence", "decode", str(example), "-t", "/nope"])
    assert res.exit_code == 2 and "topic_not_found" in res.output
    res = runner.invoke(app, ["evidence", "decode", str(tmp_path / "absent"), "-t", "/x", "--json"])
    assert res.exit_code == 2 and json.loads(res.output)["status"] == "evidence_unavailable"


# ------------------------------------------------------------------ 9. schema index

def test_schema_index_resolves_types_ignoring_constants_and_defaults():
    text = """std_msgs/Header header
uint8 LEVEL_OK=0
byte LEVEL_WARN = 1
string[] name
float64[3] fixed
float32[<=4] bounded
string<=8 short
int32 with_default 5
Header bare
geometry_msgs/Pose pose
================================================================================
MSG: std_msgs/Header
builtin_interfaces/Time stamp
string frame_id
================================================================================
MSG: geometry_msgs/Pose
float64 x
"""
    idx = cdr.parse_schema_index("pkg/msg/Thing", text)
    top = idx["pkg/msg/Thing"]
    assert set(top) == {"header", "name", "fixed", "bounded", "short", "with_default", "bare", "pose"}
    assert top["header"] == cdr.FieldType("std_msgs/msg/Header", False) and top["bare"].base == "std_msgs/msg/Header"
    assert top["name"] == cdr.FieldType("string", True) and top["fixed"] == cdr.FieldType("float64", True) and top["bounded"] == cdr.FieldType("float32", True)
    assert top["short"] == cdr.FieldType("string", False) and top["pose"].base == "geometry_msgs/msg/Pose"
    assert idx["std_msgs/msg/Header"]["stamp"].base == "builtin_interfaces/msg/Time" and "geometry_msgs/msg/Pose" in idx
    assert cdr.parse_schema_index("a/msg/B", "this is = not valid at all ???") in (None, {"a/msg/B": {}})  # never raises


def test_float32_text_is_the_shortest_that_round_trips_as_float32():
    for v in (0.1, 12.3, 0.87, 1e-7, 3.4028234663852886e38, 16777216.0, 1.0):
        f = struct.unpack("<f", struct.pack("<f", v))[0]
        assert struct.unpack("<f", struct.pack("<f", float(cdr.float32_text(f))))[0] == f
    assert cdr.float32_text(struct.unpack("<f", struct.pack("<f", 0.1))[0]) == "0.1"
    assert cdr.to_decimal(0.1, "float64") == Decimal("0.1") and cdr.to_decimal(struct.unpack("<f", struct.pack("<f", 0.1))[0], "float64") != Decimal("0.1")


# ------------------------------------------------------------------ 10. read-only and determinism

@needs_decoder
def test_decoding_and_investigating_cdr_is_read_only(example, tmp_path, monkeypatch):
    before = tree_state(example)
    contract_file = tmp_path / "c.json"
    contract_file.write_text((EXAMPLE / "expectations.json").read_text())
    inv = inspect_evidence(example)
    forbid_writes(monkeypatch)  # raises on any write, rename, delete, chmod, utime or lock
    decode_topic(inv, "/joint_states", limit=3)
    assert investigate(inv, load_contract(contract_file)).status is Status.DIVERGENCE_FOUND
    monkeypatch.undo()
    assert tree_state(example) == before


@needs_decoder
def test_output_is_deterministic(example):
    inv = inspect_evidence(example)
    a = investigation_json(investigate(inv, load_contract(EXAMPLE / "expectations.json")))
    b = investigation_json(investigate(inspect_evidence(example), load_contract(EXAMPLE / "expectations.json")))
    assert a == b
    from physicore_collector.investigator.decoding import view_json
    assert view_json(decode_topic(inv, "/odom")) == view_json(decode_topic(inv, "/odom"))
    assert "Traceback" not in a


def test_the_third_party_decoder_is_imported_only_by_the_adapter():
    src = Path(cdr.__file__).parent.parent
    offenders = [p.relative_to(src).as_posix() for p in src.rglob("*.py") if p.name != "cdr.py" and ("mcap_ros2" in p.read_text())]
    assert offenders == []
