"""DEP-01..28 and CON-17/18 (binding resolution), TEST_PLAN 6. Hostile-input heavy: every loader call must return a problems list, never raise."""
import copy
import hashlib
import json
import os
import random
import time

import pytest

from physicore_collector.localization import dependencies as D

OK = {"dependency_schema_version": 1, "declared_by": "nav team",
      "nodes": [{"id": "motor"}],
      "bindings": [{"node": "motor", "topic": "/diagnostics", "identity": {"name": "motor_driver"}}],
      "edges": [{"id": "e1", "from": "/cmd_vel", "to": "/odom", "relation": "command_response", "window_ns": 500000000},
                {"id": "e2", "from": "/odom", "to": "motor", "relation": "data_flow"}]}


def jb(d):
    return json.dumps(d).encode()


def load(d, name="d.json", **kw):
    return D.parse_dependencies(jb(d) if not isinstance(d, bytes) else d, name=name, **kw)


def mod(**over):
    d = copy.deepcopy(OK)
    for k, v in over.items():
        if v is ...:
            d.pop(k, None)
        else:
            d[k] = v
    return d


def edge(i, a, b, **kw):
    return {"id": f"e{i}", "from": a, "to": b, "relation": "dependency", **kw}


def invalid(d, name="d.json"):
    r = load(d, name)
    assert r.graph is None and r.problems and r.problems_total >= 1, r
    return r


def test_DEP_01_minimal_valid_file():
    r = load({"dependency_schema_version": 1, "declared_by": "x", "edges": [edge(1, "/a", "/b")]})
    assert r.graph is not None and r.problems == [] and len(r.graph.semantic_hash) == 64 and r.sha256 == r.graph.sha256


def test_DEP_01_full_example_valid_and_normalised():
    g = load(OK).graph
    assert [e.id for e in g.edges] == ["e1", "e2"] and g.nodes == ("motor",) and g.declared_by == "nav team"
    assert g.edges[0].window_ns == 500000000 and g.edges[1].window_ns is None


@pytest.mark.parametrize("where", ["top", "node", "binding", "edge", "identity"])
def test_DEP_02_unknown_key_rejected_at_every_level(where):
    d = copy.deepcopy(OK)
    {"top": lambda: d.update(extra=1), "node": lambda: d["nodes"][0].update(extra=1), "binding": lambda: d["bindings"][0].update(extra=1),
     "edge": lambda: d["edges"][0].update(extra=1), "identity": lambda: d["bindings"][0]["identity"].update(bogus="x")}[where]()
    r = invalid(d)
    assert any("unknown key" in p.message for p in r.problems)


@pytest.mark.parametrize("v", [..., "", "   ", 5, None])
def test_DEP_03_declared_by_required_and_meaningful(v):
    invalid(mod(declared_by=v))


def test_DEP_04_duplicate_edge_id():
    invalid(mod(edges=[edge(1, "/a", "/b"), edge(1, "/b", "/c")]))


def test_DEP_05_self_loop():
    invalid(mod(edges=[edge(1, "/a", "/a")]))
    invalid(mod(edges=[edge(1, "motor", "motor")]))


@pytest.mark.parametrize("bad", ["odom", "/has space", "/a\x01", "unknown_node", "", 5, None])
def test_DEP_06_endpoint_must_be_a_topic_or_declared_node(bad):
    invalid(mod(edges=[edge(1, bad, "/b")]))


def test_DEP_07_relation_closed_set():
    invalid(mod(edges=[{"id": "e1", "from": "/a", "to": "/b", "relation": "causes"}]))
    invalid(mod(edges=[{"id": "e1", "from": "/a", "to": "/b"}]))
    for rel in ("data_flow", "command_response", "dependency"):
        assert load(mod(edges=[{"id": "e1", "from": "/a", "to": "/b", "relation": rel}])).graph is not None


@pytest.mark.parametrize("w", [0, -1, 1.5, "5", True, None, [1]])
def test_DEP_08_window_ns_must_be_a_positive_integer(w):
    invalid(mod(edges=[edge(1, "/a", "/b", window_ns=w)]))


def test_DEP_08_yaml_float_and_exponent_forms_rejected():
    y = b"dependency_schema_version: 1\ndeclared_by: x\nedges:\n  - {id: e1, from: /a, to: /b, relation: dependency, window_ns: 5e8}\n"
    invalid(y, "d.yaml")
    y2 = y.replace(b"5e8", b"500.0")
    invalid(y2, "d.yaml")
    assert load(y.replace(b"5e8", b"500_000_000"), "d.yaml").graph.edges[0].window_ns == 500000000


def test_DEP_09_window_absent_is_valid():
    assert load(mod(edges=[edge(1, "/a", "/b")])).graph.edges[0].window_ns is None


@pytest.mark.parametrize("case", ["unknown_node", "bad_identity_key", "empty_identity", "duplicate", "empty_value", "relative_topic"])
def test_DEP_10_binding_rules(case):
    b = {"node": "motor", "topic": "/diagnostics", "identity": {"name": "m"}}
    d = mod()
    if case == "unknown_node":
        b["node"] = "ghost"
    elif case == "bad_identity_key":
        b["identity"] = {"fuzzy": "m"}
    elif case == "empty_identity":
        b["identity"] = {}
    elif case == "empty_value":
        b["identity"] = {"name": ""}
    elif case == "relative_topic":
        b["topic"] = "diagnostics"
    d["bindings"] = [b, copy.deepcopy(b)] if case == "duplicate" else [b]
    invalid(d)


def test_DEP_11_edges_required_and_non_empty():
    invalid(mod(edges=[]))
    invalid(mod(edges=...))
    invalid(mod(edges="x"))


@pytest.mark.parametrize("data,name", [(b"a: [", "x.yaml"), (b"{", "x.json"), (b"[1,2]", "x.json"), (b"", "x.yaml"), (b"", "x.json"), (b"\x00\x01\xff\xfe", "x.yaml"),
                                         (b"- a\n- b\n", "x.yaml"), (b"just text", "x.yaml"), (b"null", "x.json"), (b'"s"', "x.json")])
def test_DEP_12_unparseable_or_wrong_shape(data, name):
    invalid(data, name)


def test_DEP_13_loader_never_raises_fuzz():
    seed = 20261104
    r = random.Random(seed)
    base = jb(OK)
    for i in range(500):
        b = bytearray(base)
        for _ in range(r.randint(1, 6)):
            op = r.choice(["flip", "del", "ins", "dup"])
            pos = r.randrange(len(b))
            if op == "flip":
                b[pos] = r.randrange(256)
            elif op == "del":
                del b[pos:pos + r.randint(1, 8)]
            elif op == "ins":
                b[pos:pos] = bytes(r.randrange(256) for _ in range(r.randint(1, 8)))
            else:
                b[pos:pos] = b[pos:pos + r.randint(1, 20)]
        for name in ("f.json", "f.yaml"):
            res = D.parse_dependencies(bytes(b), name=name)
            assert (res.graph is None) != (not res.problems), f"seed={seed} i={i}"


def test_DEP_13_structural_fuzz_never_raises():
    r = random.Random(7)
    vals = [None, 0, -1, 1.5, True, "", "x", "/t", [], {}, [1], {"a": 1}, "é", "\x00", 10**30]
    for i in range(500):
        d = copy.deepcopy(OK)
        for _ in range(r.randint(1, 4)):
            cands = [d] + [x for x in (d.get("edges"), d.get("nodes"), d.get("bindings")) if isinstance(x, list) and x and isinstance(x[0], dict)]
            cands = [c if isinstance(c, dict) else c[0] for c in cands]
            if isinstance(d.get("bindings"), list) and d["bindings"] and isinstance(d["bindings"][0], dict) and isinstance(d["bindings"][0].get("identity"), dict):
                cands.append(d["bindings"][0]["identity"])
            target = r.choice(cands)
            key = r.choice(list(target)) if target and r.random() < 0.8 else "zz"
            target[key] = r.choice(vals)
        res = D.parse_dependencies(jb(d), name="f.json")
        assert (res.graph is None) != (not res.problems), f"seed=7 i={i}"


YAML_HEAD = "dependency_schema_version: 1\ndeclared_by: x\n"


@pytest.mark.parametrize("snippet,why", [
    ("edges: &a\n  - {id: e1, from: /a, to: /b, relation: dependency}\nextra: *a\n", "alias"),
    ("x: &a [1]\nedges:\n  - {id: e1, from: /a, to: /b, relation: dependency}\n", "anchor"),
    ("edges:\n  - !!str {id: e1, from: /a, to: /b, relation: dependency}\n", "tag"),
    ("edges:\n  - !!python/object/apply:os.system ['echo hi']\n", "python tag"),
    ("base: &b {relation: dependency}\nedges:\n  - {<<: *b, id: e1, from: /a, to: /b}\n", "merge"),
    ("edges:\n  - {id: e1, from: /a, to: /b, relation: dependency}\nedges:\n  - {id: e2, from: /a, to: /b, relation: dependency}\n", "duplicate key"),
    ("edges:\n  - {id: e1, id: e2, from: /a, to: /b, relation: dependency}\n", "duplicate nested key"),
    ("? [a, b]\n: c\nedges: []\n", "complex key"),
])
def test_DEP_14_yaml_features_refused(snippet, why):
    r = D.parse_dependencies((YAML_HEAD + snippet).encode(), name="d.yaml")
    assert r.graph is None and r.problems, why


def test_DEP_14_second_yaml_document_refused():
    r = D.parse_dependencies((YAML_HEAD + "edges: []\n---\nedges: []\n").encode(), name="d.yaml")
    assert r.graph is None


def test_DEP_14_billion_laughs_is_refused_quickly():
    lines = ["a0: &a0 [x, x, x, x, x, x, x, x, x]"] + [f"a{i}: &a{i} [" + ", ".join([f"*a{i-1}"] * 9) + "]" for i in range(1, 9)]
    t0 = time.monotonic()
    r = D.parse_dependencies(("\n".join(lines) + "\n").encode(), name="d.yaml")
    assert r.graph is None and time.monotonic() - t0 < 2


@pytest.mark.parametrize("data", [b'{"a": 1, "a": 2}', b'{"dependency_schema_version": NaN}', b'{"dependency_schema_version": Infinity}'])
def test_DEP_14_json_duplicate_keys_and_constants_refused(data):
    assert D.parse_dependencies(data, name="d.json").graph is None


def test_DEP_15_size_limit_boundaries():
    d = {"dependency_schema_version": 1, "declared_by": "x", "edges": [edge(1, "/a", "/b")]}
    raw = jb(d)
    pad = b" " * (1048576 - len(raw))
    assert D.parse_dependencies(raw + pad, name="d.json").graph is not None                   # exactly 1 MiB
    r = D.parse_dependencies(raw + pad + b" ", name="d.json")                                 # 1 MiB + 1 byte
    assert r.graph is None and "larger" in r.problems[0].message


def test_DEP_15_size_limit_is_configurable_downward():
    r = load(OK, limits={"dependency_file_bytes": 100})
    assert r.graph is None and "larger" in r.problems[0].message


def _chain(n):
    return {"dependency_schema_version": 1, "declared_by": "x", "edges": [edge(i, f"/t{i}", f"/t{i+1}") for i in range(n)]}


def test_DEP_16_edge_count_limit():
    assert load(_chain(5000)).graph is not None
    r = load(_chain(5001))
    assert r.graph is None and "more than 5000 edges" in r.problems[0].message


def test_DEP_17_node_count_limit():
    base = {"dependency_schema_version": 1, "declared_by": "x", "edges": [edge(1, "/a", "/b")]}
    assert load({**base, "nodes": [{"id": f"n{i}"} for i in range(5000)]}).graph is not None
    r = load({**base, "nodes": [{"id": f"n{i}"} for i in range(5001)]})
    assert r.graph is None and "more than 5000 nodes" in r.problems[0].message


def test_DEP_18_string_length_limit():
    ok = "/" + "a" * 255
    bad = "/" + "a" * 256
    assert load(mod(edges=[edge(1, ok, "/b")])).graph is not None
    assert load(mod(edges=[edge(1, bad, "/b")])).graph is None
    assert load(mod(declared_by="d" * 256)).graph is not None
    assert load(mod(declared_by="d" * 257)).graph is None


@pytest.mark.parametrize("bad", ["a\x00b", "a\x1bb", "a\nb", "a‮b", "a​b", "a\x7fb", "a\ud800b"])
def test_DEP_19_control_and_formatting_characters_refused(bad):
    assert load(mod(declared_by=bad)).graph is None
    assert load(mod(nodes=[{"id": bad}])).graph is None
    assert load(mod(edges=[edge(1, "/" + bad, "/b")])).graph is None


@pytest.mark.parametrize("bad", ["/café", "/аbc", "nа", "/a b"])
def test_ADV_11a_confusable_or_non_ascii_names_refused_in_the_dependency_file(bad):
    d = mod(edges=[edge(1, bad if bad.startswith("/") else "/a", "/b")])
    if not bad.startswith("/"):
        d = mod(nodes=[{"id": bad}])
    assert load(d).graph is None


def test_DEP_20_deep_nesting_refused_without_recursion_error():
    for depth in (50, 10_000, 100_000):
        t0 = time.monotonic()
        assert D.parse_dependencies(("[" * depth + "]" * depth).encode(), name="d.json").graph is None
        assert D.parse_dependencies(("[" * depth + "]" * depth).encode(), name="d.yaml").graph is None
        assert D.parse_dependencies(("{a: " * depth + "1" + "}" * depth).encode(), name="d.yaml").graph is None
        assert time.monotonic() - t0 < 3


def test_DEP_20_brackets_inside_strings_do_not_count_as_nesting():
    d = mod(declared_by="[[[[[[[[[[[[[[[[[[[[ and {{{{{{{{{{{{{{{{{{{{ \\\\ \" quoted")
    assert load(d).graph is not None


def test_DEP_21_special_files(tmp_path):
    good = tmp_path / "g.json"
    good.write_bytes(jb(OK))
    assert D.load_dependencies(good).graph is not None
    link = tmp_path / "link.json"
    os.symlink(good, link)
    assert D.load_dependencies(link).graph is None
    fifo = tmp_path / "f.json"
    os.mkfifo(fifo)
    t0 = time.monotonic()
    assert D.load_dependencies(fifo).graph is None and time.monotonic() - t0 < 2             # never blocks on a FIFO
    assert D.load_dependencies(tmp_path).graph is None                                         # a directory
    assert D.load_dependencies(tmp_path / "missing.json").graph is None
    big = tmp_path / "big.json"
    big.write_bytes(b" " * 2_000_000)
    assert D.load_dependencies(big).graph is None


def test_DEP_21_yaml_and_json_files_both_load(tmp_path):
    y = tmp_path / "d.yaml"
    y.write_text("dependency_schema_version: 1\ndeclared_by: x\nedges:\n  - {id: e1, from: /a, to: /b, relation: dependency}\n")
    assert D.load_dependencies(y).graph is not None


def test_DEP_22_semantic_hash_ignores_order_syntax_and_whitespace():
    a = load(OK).graph
    d = copy.deepcopy(OK)
    d["edges"].reverse()
    d["nodes"].reverse()
    d["edges"] = [dict(reversed(list(e.items()))) for e in d["edges"]]
    b = load(d).graph
    y = b"""# comment
dependency_schema_version: 1
declared_by: nav team
nodes: [ {id: motor} ]
bindings:
  - topic: /diagnostics
    node: motor
    identity: {name: motor_driver}
edges:
  - {id: e2, relation: data_flow, to: motor, from: /odom}
  - {id: e1, from: /cmd_vel, to: /odom, relation: command_response, window_ns: 500_000_000}
"""
    c = D.parse_dependencies(y, name="d.yaml").graph
    assert a.semantic_hash == b.semantic_hash == c.semantic_hash
    assert a.sha256 != c.sha256 and a.sha256 == hashlib.sha256(jb(OK)).hexdigest()           # the file digest is of the raw bytes


def _variant(f):
    d = copy.deepcopy(OK)
    f(d)
    return load(d).graph.semantic_hash


@pytest.mark.parametrize("name,f", [
    ("edge added", lambda d: d["edges"].append(edge(3, "/x", "/y"))),
    ("edge removed", lambda d: d["edges"].pop()),
    ("window changed", lambda d: d["edges"][0].update(window_ns=500000001)),
    ("window removed", lambda d: d["edges"][0].pop("window_ns")),
    ("relation changed", lambda d: d["edges"][0].update(relation="dependency")),
    ("binding changed", lambda d: d["bindings"][0]["identity"].update(name="other")),
    ("binding removed", lambda d: d.update(bindings=[])),
    ("declared_by changed", lambda d: d.update(declared_by="other team")),
    ("direction reversed", lambda d: d["edges"][0].update({"from": "/odom", "to": "/cmd_vel"})),
    ("edge id renamed", lambda d: d["edges"][0].update(id="e9")),
    ("node added", lambda d: d["nodes"].append({"id": "extra"})),
])
def test_DEP_23_semantic_hash_changes(name, f):
    assert _variant(f) != load(OK).graph.semantic_hash, name


def test_DEP_24_cycles_are_accepted():
    g = load(mod(edges=[edge(1, "/a", "/b"), edge(2, "/b", "/a")])).graph
    assert g is not None and sorted(g.ancestors("/a")) == ["/a", "/b"]


def _paths(g, u, v, **kw):
    kw.setdefault("max_depth", 32)
    kw.setdefault("max_paths", 10_000)
    kw.setdefault("budget", D.WorkBudget(10**9))
    return D.enumerate_paths(g, u, v, **kw)


def test_DEP_25_depth_bound_on_a_long_chain():
    g = load(_chain(40)).graph
    r = _paths(g, "/t0", "/t40")
    assert r.paths == [] and r.depth_truncated and r.exact_total is None                      # 40 edges > 32: not found, and flagged
    r2 = _paths(g, "/t0", "/t32")
    assert [len(p) for p in r2.paths] == [32] and r2.exact_total == 1
    r3 = _paths(g, "/t0", "/t33")
    assert r3.paths == [] and r3.depth_truncated


def _layered(layers, width):
    edges, k = [], 0
    names = [["/s"]] + [[f"/n{l}_{i}" for i in range(width)] for l in range(layers)] + [["/z"]]
    for a, b in zip(names, names[1:]):
        for x in a:
            for y in b:
                edges.append(edge(k, x, y))
                k += 1
    return {"dependency_schema_version": 1, "declared_by": "x", "edges": edges}


def test_DEP_26_per_pair_path_cap_reports_unknown_total():
    g = load(_layered(6, 5)).graph                                                            # 5^6 = 15,625 paths
    r = _paths(g, "/s", "/z", max_paths=10_000)
    assert len(r.paths) == 10_000 and r.pair_cap_reached and not r.complete and r.exact_total is None
    small = _paths(load(_layered(3, 3)).graph, "/s", "/z", max_paths=10_000)
    assert len(small.paths) == 27 and small.complete and small.exact_total == 27


def test_DEP_26_global_work_budget_bounds_total_work():
    g = load(_layered(6, 5)).graph
    b = D.WorkBudget(500)
    r = _paths(g, "/s", "/z", budget=b)
    assert r.work_exhausted and b.used == 500 and not r.complete
    r2 = _paths(g, "/s", "/z", budget=b)                                                      # an exhausted budget stays exhausted across pairs
    assert r2.paths == [] and r2.work_exhausted and b.used == 500


def test_DEP_26_enumeration_is_deterministic_and_canonical():
    g = load(_layered(3, 3)).graph
    a = [tuple(e.id for e in p) for p in _paths(g, "/s", "/z").paths]
    g2 = load({**_layered(3, 3), "edges": list(reversed(_layered(3, 3)["edges"]))}).graph
    b = [tuple(e.id for e in p) for p in _paths(g2, "/s", "/z").paths]
    assert len(a) == 27 and len(set(a)) == 27
    # same graph content with permuted edge ORDER but identical ids => identical enumeration order
    assert a == b


def test_DEP_26_paths_are_simple_in_a_cyclic_graph():
    g = load(mod(edges=[edge(1, "/a", "/b"), edge(2, "/b", "/a"), edge(3, "/b", "/c"), edge(4, "/a", "/c")])).graph
    r = _paths(g, "/a", "/c")
    assert sorted(tuple(e.id for e in p) for p in r.paths) == [("e1", "e3"), ("e4",)] and r.exact_total == 2


def test_DEP_26_unreachable_and_same_endpoint():
    g = load(mod(edges=[edge(1, "/a", "/b"), edge(2, "/c", "/d")])).graph
    assert _paths(g, "/a", "/d").paths == [] and _paths(g, "/a", "/d").exact_total == 0
    assert _paths(g, "/a", "/a").paths == []


def test_DEP_27_no_edge_is_inferred_from_names(tmp_path):
    g = load(mod(edges=[edge(1, "/cmd_vel", "/odom")], nodes=[], bindings=[])).graph
    assert [(e.src, e.dst) for e in g.edges] == [("/cmd_vel", "/odom")]
    assert g.endpoints == ["/cmd_vel", "/odom"] and g.ancestors("/cmd_vel_filtered") == []
    assert _paths(g, "/odom", "/cmd_vel").paths == []                                         # direction is never guessed


def test_DEP_27_static_loader_never_reads_recordings_or_symptoms():
    import ast
    from pathlib import Path
    src = Path(D.__file__).read_text()
    imports = {n.module or "" for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom)} | \
              {a.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Import) for a in n.names}
    assert not any(("symptom" in i) or ("anomaly" in i) or ("investigator" in i) or ("inventory" in i) for i in imports), imports


def test_DEP_28_long_chain_ancestors_and_paths_are_iterative():
    g = load(_chain(5000)).graph
    assert len(g.ancestors("/t5000")) == 5000
    assert len(_paths(g, "/t4980", "/t5000").paths) == 1
    assert _paths(g, "/t0", "/t5000").depth_truncated


# ---- bindings (CON-17/18)
def test_CON_17_binding_resolves_a_symptom_node():
    g = load(OK).graph
    assert g.node_for("/diagnostics", {"diagnostic_name": "motor_driver"}) == ("motor", "bound")
    assert g.node_for("/diagnostics", {"logger_name": "motor_driver"}) == ("motor", "bound")           # `name` matches either name kind
    assert g.node_for("/rosout", {"logger_name": "motor_driver"}) == ("/rosout", "topic")           # binding is per topic


@pytest.mark.parametrize("ident", [{"diagnostic_name": "Motor_driver"}, {"diagnostic_name": "motor_driver "}, {"diagnostic_name": " motor_driver"},
                                   {"diagnostic_name": "motor"}, {"diagnostic_name": "motor_driver_2"}, {}, {"hardware_id": "motor_driver"}])
def test_CON_18_near_misses_never_bind(ident):
    assert load(OK).graph.node_for("/diagnostics", ident) == ("/diagnostics", "topic")


def test_binding_with_two_keys_requires_both():
    d = mod(bindings=[{"node": "motor", "topic": "/diagnostics", "identity": {"name": "m", "hardware_id": "hw"}}])
    g = load(d).graph
    assert g.node_for("/diagnostics", {"diagnostic_name": "m", "hardware_id": "hw"}) == ("motor", "bound")
    assert g.node_for("/diagnostics", {"diagnostic_name": "m"}) == ("/diagnostics", "topic")
    assert g.node_for("/diagnostics", {"diagnostic_name": "m", "hardware_id": "other"}) == ("/diagnostics", "topic")


def test_ambiguous_bindings_fail_closed_and_same_node_is_not_ambiguous():
    d = mod(nodes=[{"id": "a"}, {"id": "b"}], edges=[edge(1, "/a", "/b")],
            bindings=[{"node": "a", "topic": "/d", "identity": {"name": "x"}}, {"node": "b", "topic": "/d", "identity": {"hardware_id": "h"}}])
    g = load(d).graph
    assert g.node_for("/d", {"diagnostic_name": "x", "hardware_id": "h"}) == ("/d", "ambiguous")
    assert g.node_for("/d", {"diagnostic_name": "x"}) == ("a", "bound")
    d2 = mod(nodes=[{"id": "a"}], edges=[edge(1, "/a", "/b")], bindings=[{"node": "a", "topic": "/d", "identity": {"name": "x"}}, {"node": "a", "topic": "/d", "identity": {"hardware_id": "h"}}])
    assert load(d2).graph.node_for("/d", {"diagnostic_name": "x", "hardware_id": "h"}) == ("a", "bound")


def test_ADV_25_node_id_cannot_collide_with_a_topic():
    invalid(mod(nodes=[{"id": "/odom"}], bindings=[], edges=[edge(1, "/odom", "/b")]))


def test_ADV_23_declared_by_limits():
    invalid(mod(declared_by="x" * 300))
    invalid(mod(declared_by="a\x07b"))


def test_problem_list_is_bounded_but_total_is_exact():
    d = mod(edges=[{"id": f"e{i}", "from": "bad", "to": "worse", "relation": "x"} for i in range(200)])
    r = load(d)
    assert r.graph is None and len(r.problems) == 100 and r.problems_total > 100


def test_problem_paths_point_at_the_offending_field():
    r = load(mod(edges=[edge(1, "/a", "/b", window_ns=0)]))
    assert [p.path for p in r.problems] == ["$.edges[0].window_ns"]


# ---- strengthening after the first Step-3 mutation run (each kills a named survivor; messages are asserted so redundant defences are exercised)
def msgs(r):
    return " | ".join(p.message for p in r.problems)


@pytest.mark.parametrize("v", [2, 0, "1", True, 1.0, None])
def test_schema_version_must_be_the_integer_one(v):
    r = invalid(mod(dependency_schema_version=v))
    assert "must be the integer 1" in msgs(r)
    assert "required" in msgs(invalid(mod(dependency_schema_version=...)))


def test_declared_by_missing_is_reported_as_required():
    assert "required" in msgs(invalid(mod(declared_by=...)))


def test_duplicate_node_id_refused():
    r = invalid(mod(nodes=[{"id": "n"}, {"id": "n"}], bindings=[], edges=[edge(1, "/a", "/b")]))
    assert "duplicate node id" in msgs(r)


def test_nul_bytes_named_in_the_problem():
    assert "NUL" in msgs(invalid(b'{"a": "x\x00y"}'))


def test_yaml_explicit_tag_refused_even_when_the_content_would_be_valid():
    y = b"dependency_schema_version: 1\ndeclared_by: !!str team\nedges:\n  - {id: e1, from: /a, to: /b, relation: dependency}\n"
    r = D.parse_dependencies(y, name="d.yaml")
    assert r.graph is None and "tags" in msgs(r)


def test_yaml_inline_merge_key_refused_even_without_an_alias():
    y = b"dependency_schema_version: 1\ndeclared_by: x\nedges:\n  - {<<: {relation: dependency}, id: e1, from: /a, to: /b}\n"
    r = D.parse_dependencies(y, name="d.yaml")
    assert r.graph is None and "merge" in msgs(r)


def test_yaml_aliases_and_anchors_named_in_the_problem():
    r = D.parse_dependencies((YAML_HEAD + "edges: &a []\nx: *a\n").encode(), name="d.yaml")
    assert "anchors" in msgs(r)


@pytest.mark.parametrize("name,text", [("d.yaml", "[" * 100 + "]" * 100), ("d.json", "[" * 100 + "]" * 100)])
def test_nesting_limit_is_the_reported_reason(name, text):
    r = D.parse_dependencies(text.encode(), name=name)
    assert r.graph is None and "nesting deeper than 8" in msgs(r)


def test_json_duplicate_key_in_an_otherwise_valid_file_is_refused():
    raw = b'{"dependency_schema_version": 1, "declared_by": "x", "declared_by": "y", "edges": [{"id": "e1", "from": "/a", "to": "/b", "relation": "dependency"}]}'
    r = D.parse_dependencies(raw, name="d.json")
    assert r.graph is None and "duplicate key" in msgs(r)


def test_json_nan_named_in_the_problem():
    raw = b'{"dependency_schema_version": 1, "declared_by": "x", "edges": [{"id": "e1", "from": "/a", "to": "/b", "relation": "dependency", "window_ns": NaN}]}'
    r = D.parse_dependencies(raw, name="d.json")
    assert r.graph is None and "not allowed" in msgs(r)


def test_symlink_is_named_in_the_problem(tmp_path):
    good = tmp_path / "g.json"
    good.write_bytes(jb(OK))
    link = tmp_path / "l.json"
    os.symlink(good, link)
    assert "symbolic link" in msgs(D.load_dependencies(link))


def test_empty_file_is_reported_as_empty():
    assert "empty" in msgs(invalid(b"", "d.yaml"))
    assert "empty" in msgs(invalid(b"# only a comment\n", "d.yaml"))


def test_file_digest_is_of_the_raw_bytes_including_whitespace():
    raw = jb(OK) + b"\n\n  "
    r = D.parse_dependencies(raw, name="d.json")
    assert r.graph is not None and r.sha256 == r.graph.sha256 == hashlib.sha256(raw).hexdigest()


def test_semantic_hash_is_independent_of_the_order_of_edges_in_the_file():
    a = load(mod(edges=[edge(1, "/a", "/b"), edge(2, "/b", "/c"), edge(3, "/c", "/d")])).graph
    b = load(mod(edges=[edge(3, "/c", "/d"), edge(1, "/a", "/b"), edge(2, "/b", "/c")])).graph
    assert a.semantic_hash == b.semantic_hash and [e.id for e in b.edges] == ["e1", "e2", "e3"]


def test_dead_ends_cost_no_work():
    edges_ = [edge(0, "/a", "/b")] + [edge(i, "/a", f"/x{i}") for i in range(1, 51)] + [edge(100 + i, f"/x{i}", f"/y{i}") for i in range(1, 51)]
    g = load(mod(edges=edges_)).graph
    b = D.WorkBudget(1000)
    r = _paths(g, "/a", "/b", budget=b)
    assert [e.id for e in r.paths[0]] == ["e0"] and b.used == 1 and r.exact_total == 1


def test_enumeration_order_is_by_destination_then_edge_id():
    g = load(mod(edges=[edge(1, "/s", "/m2"), edge(2, "/s", "/m1"), edge(3, "/m1", "/z"), edge(4, "/m2", "/z"), edge(5, "/s", "/z")])).graph
    got = [tuple(e.id for e in p) for p in _paths(g, "/s", "/z").paths]
    assert got == [("e2", "e3"), ("e1", "e4"), ("e5",)]            # dst order /m1 < /m2 < /z


def test_work_budget_semantics():
    b = D.WorkBudget(2)
    assert not b.exhausted and b.take() and b.take() and b.exhausted and not b.take() and b.used == 2
