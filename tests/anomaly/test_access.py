import pytest

from physicore_collector.anomaly import access, ros_constants
from physicore_collector.anomaly.access import DecodeCounter, DecodeFailure, MessageView, array_length, iter_leaves, schema_constants
from physicore_collector.anomaly.clock import LOG_TIME_DOMAIN
from tests import cdr_fixtures as cf
from tests import investigator_fixtures as F
from tests.anomaly import fixtures as X


def test_normalise_type():
    assert access.normalise_type("rcl_interfaces/Log") == "rcl_interfaces/msg/Log"
    assert access.normalise_type("rcl_interfaces/msg/Log") == "rcl_interfaces/msg/Log"
    assert access.normalise_type(None) is None and access.normalise_type("") is None
    assert access.normalise_type("a/b/c/d") == "a/b/c/d"   # no heuristic rewriting
    assert access.normalise_type("a/b/c") == "a/b/c"
    assert access.normalise_type("pkg/msgs") == "pkg/msg/msgs"


def test_schema_constants_root_and_nested_sections():
    text = (b"byte OK=0\nbyte WARN=1\nbyte level\nnot/Nested[] kids\n" + b"=" * 80 + b"\nMSG: not/Nested\nuint8 FOO=7\nstring x\n")
    got = schema_constants(text, "pkg/Root")
    assert got == {"pkg/msg/Root": {"OK": 0, "WARN": 1}, "not/msg/Nested": {"FOO": 7}}


def test_schema_constants_garbage_never_raises():
    assert schema_constants(b"\xff\xfe\x00", "a/b") == {}
    assert schema_constants(None, "a/b") == {}
    assert schema_constants("string S=abc\nint32 X=notanumber\n", "a/b") == {}


def test_builtin_table_matches_recorded_schemas():
    ts = cf.typestore()
    checked = 0
    for tname, consts in ros_constants.TABLE.items():
        if tname not in ts.types:
            continue   # rosgraph_msgs/Log is not in the ROS 2 type store: JSON-only in v1
        checked += 1
        got = schema_constants(cf.schema_text(tname), tname).get(tname, {})
        assert got, tname
        for k, v in consts.items():
            assert got.get(k) == v, (tname, k)
    assert checked == 5


def test_leaf_walker_json_full_scan_no_early_exit():
    root = {"a": 1, "b": [1.5, {"c": "s"}, float("nan")], "d": True, "e": None}
    got = [(l.path, l.kind) for l in iter_leaves(root)]
    assert got == [("a", "int"), ("b[0]", "float"), ("b[1].c", "str"), ("b[2]", "float"), ("d", "bool"), ("e", "none")]


def test_leaf_walker_selector_and_failure():
    root = {"a": {"x": 1}, "b": {"x": 2}}
    assert [l.path for l in iter_leaves(root, "b")] == ["b.x"]
    assert list(iter_leaves(DecodeFailure("x"))) == []


def test_leaf_walker_bytes_is_one_leaf():
    got = list(iter_leaves({"u": b"\x00\x01"}))
    assert len(got) == 1 and got[0].kind == "bytes"


def test_array_length():
    root = {"r": [1, 2, 3], "s": "abc", "n": {"v": []}}
    assert array_length(root, "r") == 3
    assert array_length(root, "n.v") == 0
    assert array_length(root, "s") is None          # a string is not an array
    assert array_length(root, "nope") is None
    assert array_length(DecodeFailure("x"), "r") is None


def test_view_decodes_once_and_counts(tmp_path):
    bag = X.write_bag(tmp_path / "b", [{"/t": [(F.BASE, {"v": 1}), (F.BASE + F.MS, {"v": 2})]}])
    inv, ev = X.load(bag, {"/t"})
    counter = DecodeCounter()
    views = [MessageView(m, ev, counter) for m in ev.messages["/t"]]
    assert counter.total == 0                        # nothing decoded until asked
    for v in views:
        v.decoded(); v.decoded(); list(v.iter_leaves())
    assert counter.total == 2 and counter.max_per_message() == 1
    assert views[0].root() == {"v": 1}
    assert views[0].payload_kind == "json"
    assert views[0].log_time.domain == LOG_TIME_DOMAIN and views[0].log_time.value_ns == F.BASE


def test_view_json_unparseable_is_failure(tmp_path):
    bag = F.write_json_bag(tmp_path / "b", [{"/t": [(F.BASE, {"v": 1})]}])
    inv, ev = X.load(bag, {"/t"})
    m = ev.messages["/t"][0]
    m.data = b"{not json"
    v = MessageView(m, ev, DecodeCounter())
    assert isinstance(v.decoded(), DecodeFailure) and v.decoded().kind == "json_unparseable"
    assert v.stamp("header.stamp")[0] is None


def test_view_stamp_json_rules(tmp_path):
    good = {"header": {"stamp": {"sec": 3, "nanosec": 7}}}
    neg = {"header": {"stamp": {"sec": -1, "nanosec": 7}}}
    bad_ns = {"header": {"stamp": {"sec": 1, "nanosec": 10**9}}}
    zero = {"header": {"stamp": {"sec": 0, "nanosec": 0}}}
    bag = F.write_json_bag(tmp_path / "b", [{"/t": [(F.BASE + i, p) for i, p in enumerate([good, neg, bad_ns, zero])]}])
    inv, ev = X.load(bag, {"/t"})
    c = DecodeCounter()
    got = [MessageView(m, ev, c).stamp("header.stamp")[0] for m in ev.messages["/t"]]
    assert got == [3_000_000_007, None, None, 0]
    label = MessageView(ev.messages["/t"][0], ev, c).stamp("header.stamp")[1]
    assert label.startswith("structure_only")


def test_view_cdr_stamp_and_leaves(tmp_path):
    pytest.importorskip("rosbags")
    from physicore_collector.investigator.cdr import decoder_info
    if not decoder_info().available:
        pytest.skip("decoder not installed")
    bag = cf.write_cdr_mcap(tmp_path / "r.mcap", [("/b", "sensor_msgs/msg/BatteryState", [(F.BASE, cf.battery(5_000_000_123, 12.0, 0.5))])])
    inv, ev = X.load(bag, {"/b"})
    v = MessageView(ev.messages["/b"][0], ev, DecodeCounter())
    assert v.payload_kind == "cdr"
    assert v.stamp("header.stamp")[0] == 5_000_000_123
    assert v.stamp("header.stamp")[1].startswith("schema_verified")
    paths = [l.path for l in v.iter_leaves()]
    assert "voltage" in paths and "header.stamp.sec" in paths
    assert v.array_length("cell_voltage") == 0


def test_topic_schema_names_cdr_exact(tmp_path):
    pytest.importorskip("rosbags")
    from physicore_collector.anomaly.runner import topic_schema_names
    from physicore_collector.inventory import inspect_evidence
    p = cf.write_cdr_mcap(tmp_path / "r.mcap", [("/rosout", "rcl_interfaces/msg/Log", [])])
    assert topic_schema_names(inspect_evidence(p)) == {"/rosout": {"rcl_interfaces/msg/Log"}}
