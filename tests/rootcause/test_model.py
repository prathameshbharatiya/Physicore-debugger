"""MOD (M4a): the declared system model: strict, bounded, hostile-input safe, deterministic."""
import copy
import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

from physicore_collector.rootcause import model as M
from tests.rootcause import mfx

import physicore_collector

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(physicore_collector.__file__).resolve().parents[1]


def parse(d, name="m.json", **kw):
    return M.parse_system_model(mfx.jbytes(d), name=name, limits=kw or None)


def bad(d, *needles, name="m.json"):
    r = parse(d, name)
    assert r.model is None and r.problems, "expected the model to be rejected"
    text = " | ".join(f"{p.path}: {p.message}" for p in r.problems)
    for n in needles:
        assert n in text, (n, text)
    return r


def test_MOD_01_a_valid_model_loads_and_every_item_carries_who_declared_it():
    r = parse(mfx.valid())
    assert r.problems == [] and r.model is not None
    c = r.model.content
    assert M.SCHEMA_VERSION == 1 and c["system_name"] == "test robot" and c["operating_context"] == "indoor warehouse\nflat floor"
    assert [x["id"] for x in c["components"]] == ["base", "lidar", "nav", "pc"]
    nav = next(x for x in c["components"] if x["id"] == "nav")
    lidar = next(x for x in c["components"] if x["id"] == "lidar")
    assert (nav["declared_by"], nav["declared_by_source"]) == ("nav lead (test)", "item")
    assert (lidar["declared_by"], lidar["declared_by_source"]) == ("ops team (test)", "file")
    for key in ("components", "bindings", "versions", "constraints", "interventions", "edges"):
        assert c[key] and all(x["declared_by"] and x["declared_by_source"] in ("item", "file") for x in c[key]), key
    assert r.model.topics() == {"/lidar/scan", "/cmd_mode", "/robot_state", "/diagnostics"}


def test_MOD_02_json_and_yaml_say_the_same_thing_and_the_digest_ignores_layout():
    d = mfx.valid()
    a = M.parse_system_model(mfx.jbytes(d), name="a.json").model
    b = M.parse_system_model(mfx.ybytes(d), name="a.yaml").model
    shuffled = copy.deepcopy(d)
    for k in ("components", "bindings", "constraints", "edges", "interventions", "versions"):
        shuffled[k].reverse()
    shuffled = dict(reversed(list(shuffled.items())))
    c = M.parse_system_model(json.dumps(shuffled, indent=4).encode(), name="c.json").model
    assert a.digest == b.digest == c.digest and a.content == b.content == c.content
    assert len({a.file_sha256, b.file_sha256, c.file_sha256}) == 3
    changed = parse(mfx.mutated(lambda x: x["components"][0].update(role="driver"))).model
    assert changed.digest != a.digest


def test_MOD_03_every_required_key_is_required():
    d = mfx.valid()
    for k in ("system_model_schema_version", "declared_by", "system_name"):
        bad(mfx.mutated(lambda x, k=k: x.pop(k)), f"$.{k}", "required")
    for section, keys in (("components", ("id", "role")), ("bindings", ("component", "role", "topic")), ("versions", ("component", "kind", "name", "version")),
                          ("interventions", ("id", "kind", "component", "at_ns", "clock_domain")), ("edges", ("id", "from", "to", "relation"))):
        for k in keys:
            bad(mfx.mutated(lambda x, s=section, k=k: x[s][0 if s != "interventions" else 0].pop(k)), f"$.{section}[0].{k}", "required")
    bad(mfx.mutated(lambda x: x["constraints"][0].pop("id")), "required")
    bad(mfx.mutated(lambda x: x["constraints"][0].pop("kind")), "$.constraints[0].kind", "required")
    bad(mfx.mutated(lambda x: x["constraints"][2].pop("deadline_ns")), "deadline_ns", "required")
    bad(mfx.mutated(lambda x: x["constraints"][2].pop("trigger")), "trigger", "required")
    bad(mfx.mutated(lambda x: x["constraints"][3].pop("members")), "members", "required")
    bad(mfx.mutated(lambda x: x["constraints"][3].pop("min_healthy")), "min_healthy", "required")
    assert d  # the original is untouched


def test_MOD_04_unknown_keys_are_rejected_everywhere():
    for path, fn in (("$.surprise", lambda x: x.update(surprise=1)),
                     ("$.components[0].colour", lambda x: x["components"][0].update(colour="red")),
                     ("$.bindings[0].qos", lambda x: x["bindings"][0].update(qos="reliable")),
                     ("$.versions[0].licence", lambda x: x["versions"][0].update(licence="x")),
                     ("$.interventions[0].who", lambda x: x["interventions"][0].update(who="x")),
                     ("$.edges[0].weight", lambda x: x["edges"][0].update(weight=2)),
                     ("$.constraints[0].min_hz", lambda x: x["constraints"][0].update(min_hz=1)),         # a key of another kind
                     ("$.constraints[1].min", lambda x: x["constraints"][1].update(min=1)),
                     ("$.constraints[2].trigger.extra", lambda x: x["constraints"][2]["trigger"].update(extra=1)),
                     ("$.constraints[3].role", lambda x: x["constraints"][3].update(role="x"))):
        bad(mfx.mutated(fn), path, "unknown key")


def test_MOD_05_the_schema_version_must_be_the_integer_one():
    for v in (2, 0, "1", True, 1.0, None, [1]):
        bad(mfx.mutated(lambda x, v=v: x.update(system_model_schema_version=v)), "$.system_model_schema_version")


def test_MOD_06_closed_lists_are_enforced():
    bad(mfx.mutated(lambda x: x["components"][0].update(role="wizard")), "role", "must be one of")
    bad(mfx.mutated(lambda x: x["components"][0].update(role=3)), "role")
    bad(mfx.mutated(lambda x: x["versions"][0].update(kind="firmware")), "kind", "must be one of")
    bad(mfx.mutated(lambda x: x["constraints"][0].update(kind="vibes")), "kind", "must be one of")
    bad(mfx.mutated(lambda x: x["interventions"][0].update(kind="reboot")), "kind", "must be one of")
    bad(mfx.mutated(lambda x: x["interventions"][0].update(clock_domain="header_stamp")), "clock_domain", "must be one of")
    bad(mfx.mutated(lambda x: x["edges"][0].update(relation="causes")), "relation", "must be one of")
    assert set(M.ROLES) == {"sensor", "controller", "actuator", "planner", "driver", "middleware", "compute", "power"}


def test_MOD_07_references_must_resolve():
    bad(mfx.mutated(lambda x: x["bindings"][0].update(component="ghost")), "must name a declared component")
    bad(mfx.mutated(lambda x: x["versions"][0].update(component="ghost")), "must name a declared component")
    bad(mfx.mutated(lambda x: x["interventions"][0].update(component="ghost")), "must name a declared component")
    bad(mfx.mutated(lambda x: x["constraints"][0].update(role="ghost")), "no binding for component 'lidar' and role 'ghost'")
    bad(mfx.mutated(lambda x: x["constraints"][2]["response"].update(component="ghost")), "no binding for component 'ghost'")
    bad(mfx.mutated(lambda x: x["constraints"][3].update(members=["lidar", "ghost"])), "must name a declared component")
    bad(mfx.mutated(lambda x: x["edges"][0].update(to="ghost")), "neither a topic")
    bad(mfx.mutated(lambda x: x["edges"][0].update(**{"from": "nav", "to": "nav"})), "to itself")


def test_MOD_08_duplicates_are_rejected():
    bad(mfx.mutated(lambda x: x["components"].append(dict(x["components"][0]))), "duplicate component id")
    bad(mfx.mutated(lambda x: x["bindings"].append(dict(x["bindings"][0], topic="/other"))), "duplicate binding")
    bad(mfx.mutated(lambda x: x["versions"].append(dict(x["versions"][0], version="9"))), "duplicate version entry")
    bad(mfx.mutated(lambda x: x["constraints"].append(dict(x["constraints"][0]))), "duplicate constraint id")
    bad(mfx.mutated(lambda x: x["interventions"].append(dict(x["interventions"][0]))), "duplicate intervention id")
    bad(mfx.mutated(lambda x: x["edges"].append(dict(x["edges"][0]))), "duplicate edge id")


@pytest.mark.parametrize("fn,needle", [
    (lambda x: x["bindings"][0].update(topic="lidar/scan"), "topic starts with '/'"),
    (lambda x: x["bindings"][0].update(topic="/a b"), "topic starts with '/'"),
    (lambda x: x["bindings"][0].update(field="ranges[0]"), "dotted path"),
    (lambda x: x["bindings"][0].update(field=".ranges"), "dotted path"),
    (lambda x: x["bindings"][0].update(type="LaserScan"), "pkg/msg/Name"),
    (lambda x: x["versions"][0].update(digest="AB" * 32), "64 lowercase"),
    (lambda x: x["versions"][0].update(digest="ab" * 31), "64 lowercase"),
    (lambda x: x.update(dependency_file_sha256="xyz"), "64 lowercase"),
    (lambda x: x["components"][0].update(id="/lidar"), "does not start with '/'"),
    (lambda x: x["components"][0].update(id=""), "uses only letters"),
    (lambda x: x["components"][0].update(id=5), "must be a string"),
    (lambda x: x.update(declared_by=""), "must not be empty"),
    (lambda x: x.update(declared_by="  "), "must not be empty"),
    (lambda x: x["components"][1].update(declared_by=""), "must not be empty"),
    (lambda x: x.update(system_name="  "), "must not be empty"),
    (lambda x: x["versions"][0].update(version=""), "must not be empty"),
    (lambda x: x.update(system_name="a‮b"), "control or formatting"),
    (lambda x: x["components"][0].update(description="a\tb"), "control or formatting"),
    (lambda x: x.update(operating_context="a\x07b"), "control or formatting"),
    (lambda x: x.update(system_name="x" * 257), "longer than 256"),
    (lambda x: x.update(operating_context="x" * 2001), "longer than 2000"),
    (lambda x: x["edges"][0].update(window_ns=0), "positive integer"),
    (lambda x: x["edges"][0].update(window_ns=1.5), "positive integer"),
    (lambda x: x["edges"][0].update(window_ns=True), "positive integer"),
])
def test_MOD_09_value_formats(fn, needle):
    bad(mfx.mutated(fn), needle)


def test_MOD_10_the_operating_context_may_hold_newlines_but_nothing_else_invisible():
    assert parse(mfx.mutated(lambda x: x.update(operating_context="a\nb\nc"))).model is not None
    assert parse(mfx.mutated(lambda x: x.pop("operating_context"))).model.content["operating_context"] is None


@pytest.mark.parametrize("fn,needle", [
    (lambda x: x["constraints"][0].update(min=5, max=1), "greater than max"),
    (lambda x: x["constraints"][0].update(min=True), "finite number"),
    (lambda x: x["constraints"][0].update(max="9"), "finite number"),
    (lambda x: (x["constraints"][0].pop("min"), x["constraints"][0].pop("max")), "at least one of min and max"),
    (lambda x: x["constraints"][1].update(min_hz=-1), "must not be negative"),
    (lambda x: x["constraints"][1].update(min_hz=20), "greater than max_hz"),
    (lambda x: (x["constraints"][1].pop("min_hz"), x["constraints"][1].pop("max_hz")), "at least one of min_hz and max_hz"),
    (lambda x: x["constraints"][2].update(deadline_ns=0), "positive integer"),
    (lambda x: x["constraints"][2].update(deadline_ns=1.5), "positive integer"),
    (lambda x: x["constraints"][2].update(deadline_ns=False), "positive integer"),
    (lambda x: x["constraints"][3].update(members=["lidar"]), "at least two"),
    (lambda x: x["constraints"][3].update(members=["lidar", "lidar"]), "duplicate member"),
    (lambda x: x["constraints"][3].update(members="lidar"), "at least two"),
    (lambda x: x["constraints"][3].update(min_healthy=0), "from 1 to the number"),
    (lambda x: x["constraints"][3].update(min_healthy=3), "from 1 to the number"),
    (lambda x: x["constraints"][3].update(min_healthy=True), "from 1 to the number"),
    (lambda x: x["interventions"][1].update(parameter="x"), "a restart has no parameter"),
    (lambda x: x["interventions"][0].pop("parameter"), "required for a parameter change"),
    (lambda x: x["interventions"][0].update(at_ns=-1), "non-negative"),
    (lambda x: x["interventions"][0].update(at_ns=True), "non-negative"),
    (lambda x: x["interventions"][0].update(at_ns=1.5), "non-negative"),
])
def test_MOD_11_numbers_and_shapes_of_constraints_and_interventions(fn, needle):
    bad(mfx.mutated(fn), needle)


def test_MOD_12_non_finite_numbers_are_refused_in_yaml_and_json():
    y = mfx.ybytes(mfx.valid()).decode().replace("max: 12.5", "max: .inf")
    r = M.parse_system_model(y.encode(), name="m.yaml")
    assert r.model is None and "finite number" in r.problems[0].message
    j = mfx.jbytes(mfx.valid()).decode().replace("12.5", "NaN")
    r = M.parse_system_model(j.encode(), name="m.json")
    assert r.model is None and "constant NaN" in r.problems[0].message


def test_MOD_13_one_bad_item_makes_the_whole_file_invalid_and_all_problems_are_listed():
    d = mfx.mutated(lambda x: (x["components"][0].update(role="wizard"), x["edges"][0].update(relation="x"), x.update(declared_by="")))
    r = bad(d)
    assert len(r.problems) >= 3 and r.model is None


def test_MOD_14_the_listed_problems_are_capped_and_the_total_is_counted():
    d = mfx.mutated(lambda x: x["components"].extend({"id": f"c{i}", "role": "wizard"} for i in range(300)))
    r = parse(d)
    assert len(r.problems) == 100 and r.problems_total >= 300 and r.model is None


def test_MOD_15_size_limits_apply_per_section():
    big = mfx.mutated(lambda x: x["components"].extend({"id": f"c{i}", "role": "sensor"} for i in range(6)))
    bad_ = parse(big, components=5)
    assert bad_.model is None and "more than 5 entries" in bad_.problems[0].message
    r = M.parse_system_model(b"x" * 100, name="m.json", limits={"file_bytes": 50})
    assert r.model is None and "larger than 50 bytes" in r.problems[0].message


HOSTILE = {
    "empty": b"", "only_spaces": b"   \n", "null": b"null", "list": b"[1,2]", "scalar": b"5", "nul": b"{\x00}", "bad_utf8": b"\xff\xfe{",
    "yaml_alias": b"a: &x 1\nb: *x\n", "yaml_anchor": b"a: &x 1\n", "yaml_tag": b"a: !!str 1\n", "yaml_merge": b"a: {<<: {b: 1}}\n",
    "yaml_dup": b"a: 1\na: 2\n", "yaml_two_docs": b"a: 1\n---\nb: 2\n", "yaml_deep": b"a: " + b"[" * 50 + b"]" * 50, "yaml_syntax": b"a: [1, 2\n",
    "yaml_nonscalar_key": b"? [a]\n: 1\n", "json_dup": b'{"a":1,"a":2}', "json_deep": b"[" * 50 + b"]" * 50, "json_syntax": b'{"a":',
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_MOD_16_hostile_files_are_rejected_without_raising(name):
    for fname in ("m.yaml", "m.json"):
        r = M.parse_system_model(HOSTILE[name], name=fname)
        assert r.model is None and r.problems and r.sha256


def test_MOD_17_random_bytes_and_random_edits_never_raise_and_never_accept_garbage():
    rng = random.Random(7)
    base = mfx.jbytes(mfx.valid())
    for _ in range(300):
        data = bytearray(base)
        for _ in range(rng.randint(1, 4)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        r = M.parse_system_model(bytes(data), name="m.json")
        assert (r.model is None) == bool(r.problems)
    for _ in range(100):
        r = M.parse_system_model(bytes(rng.randrange(256) for _ in range(rng.randint(0, 200))), name="m.yaml")
        assert (r.model is None) == bool(r.problems)


def test_MOD_18_wrong_container_types_are_problems_not_exceptions():
    for key in ("components", "bindings", "versions", "constraints", "interventions", "edges"):
        bad(mfx.mutated(lambda x, k=key: x.update({k: "nope"})), "must be a list")
        bad(mfx.mutated(lambda x, k=key: x.update({k: [5]})), "must be a mapping")
    bad(mfx.mutated(lambda x: x["constraints"][2].update(trigger=5)), "must be a mapping with component and role")
    bad(mfx.mutated(lambda x: x["constraints"][2].update(trigger={})), "required")


def test_MOD_19_files_are_read_only_when_regular_and_never_followed_through_links(tmp_path):
    p = mfx.write(tmp_path, mfx.valid())
    assert M.load_system_model(p).model is not None
    link = tmp_path / "link.json"
    link.symlink_to(p)
    r = M.load_system_model(link)
    assert r.model is None and "symbolic link" in r.problems[0].message
    r = M.load_system_model(tmp_path)
    assert r.model is None and "not a regular file" in r.problems[0].message
    fifo = tmp_path / "f.json"
    os.mkfifo(fifo)
    r = M.load_system_model(fifo)
    assert r.model is None and "not a regular file" in r.problems[0].message
    r = M.load_system_model(tmp_path / "missing.json")
    assert r.model is None and "cannot read" in r.problems[0].message
    big = tmp_path / "big.json"
    big.write_bytes(b"x" * 2_000_000)
    assert "larger than" in M.load_system_model(big).problems[0].message


def test_MOD_20_the_digest_does_not_depend_on_the_hash_seed():
    code = ("import sys;from physicore_collector.rootcause import model as M;from tests.rootcause import mfx;"
            "r=M.parse_system_model(mfx.ybytes(mfx.valid()),name='m.yaml');sys.stdout.write(r.model.digest+r.model.file_sha256)")
    outs = []
    for seed in ("0", "99"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": f"{SRC}:{ROOT}", "LC_ALL": "C"}
        outs.append(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT, check=True).stdout)
    assert outs[0] == outs[1] and len(outs[0]) == 128


def test_MOD_21_the_model_content_holds_only_declared_data_and_nothing_inferred():
    c = parse(mfx.valid()).model.content
    assert set(c) == {"system_model_schema_version", "declared_by", "system_name", "operating_context", "dependency_file_sha256", "components", "bindings",
                      "versions", "constraints", "interventions", "edges"}
    assert all(set(x) == {"id", "role", "description", "declared_by", "declared_by_source"} for x in c["components"])


def test_MOD_22_an_edge_topic_endpoint_is_checked_like_any_topic():
    bad(mfx.mutated(lambda x: x["edges"][1].update(**{"from": "/bad topic"})), "a topic uses only letters")


def test_MOD_23_a_newline_is_allowed_only_in_the_operating_context():
    for key in ("system_name", "declared_by"):
        bad(mfx.mutated(lambda x, k=key: x.update({k: "a\nb"})), "control or formatting")
    bad(mfx.mutated(lambda x: x["components"][0].update(description="a\nb")), "control or formatting")


def test_MOD_24_the_file_size_limit_is_exact():
    d = mfx.jbytes(mfx.valid())
    ok = d + b" " * (4000 - len(d))
    assert M.parse_system_model(ok, name="m.json", limits={"file_bytes": 4000}).model is not None
    r = M.parse_system_model(ok + b" ", name="m.json", limits={"file_bytes": 4000})
    assert r.model is None and "larger than 4000 bytes" in r.problems[0].message


def test_MOD_25_invalid_utf8_inside_an_otherwise_valid_file_is_refused():
    d = mfx.jbytes(mfx.valid()).replace(b"test robot", b"test \xff robot")
    r = M.parse_system_model(d, name="m.json")
    assert r.model is None and "not valid UTF-8" in r.problems[0].message


def test_MOD_26_the_empty_file_has_its_own_message():
    for name in ("m.json", "m.yaml"):
        assert M.parse_system_model(b"null", name=name).problems[0].message == "the file is empty"
        assert M.parse_system_model(b"", name="m.yaml").problems[0].message == "the file is empty"


def test_MOD_27_json_duplicate_keys_in_an_otherwise_valid_model_are_refused():
    d = mfx.jbytes(mfx.valid()).decode()
    dup = d.replace('"system_name": "test robot"', '"system_name": "test robot", "system_name": "other"')
    assert dup != d
    r = M.parse_system_model(dup.encode(), name="m.json")
    assert r.model is None and "duplicate key 'system_name'" in r.problems[0].message


@pytest.mark.parametrize("edit,needle", [
    (lambda y: y.replace("system_name: test robot", "system_name: &n test robot\nx_alias: *n"), "anchors"),   # an alias always follows an anchor, which is refused first
    (lambda y: y.replace("system_name: test robot", "system_name: &n test robot"), "anchors"),
    (lambda y: y.replace("system_name: test robot", "system_name: !!str test robot"), "tags"),
    (lambda y: y.replace("system_name: test robot", "system_name: test robot\nsystem_name: other"), "duplicate key"),
    (lambda y: y + "\n---\nextra: 1\n", "more than one YAML document"),
    (lambda y: y.replace("system_name: test robot", "<<: {a: 1}\nsystem_name: test robot"), "merge keys"),
])
def test_MOD_28_yaml_features_that_hide_or_repeat_content_are_refused_in_an_otherwise_valid_model(edit, needle):
    y = mfx.ybytes(mfx.valid()).decode()
    changed = edit(y)
    assert changed != y
    r = M.parse_system_model(changed.encode(), name="m.yaml")
    assert r.model is None and needle in r.problems[0].message, r.problems


def test_MOD_29_deep_nesting_is_refused_before_it_is_parsed():
    r = M.parse_system_model(b"[" * 100000 + b"]" * 100000, name="m.json")
    assert r.model is None and "nesting deeper than" in r.problems[0].message
    r = M.parse_system_model(b"a: " + b"[" * 100000 + b"]" * 100000, name="m.yaml")
    assert r.model is None and "nesting deeper than" in r.problems[0].message
