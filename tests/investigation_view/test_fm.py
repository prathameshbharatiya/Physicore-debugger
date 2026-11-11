"""FM: integer-only formatting (FR-VW-07) and the static absence of floating point."""
import ast
from fractions import Fraction
from pathlib import Path

import pytest

import physicore_collector.investigation_view as pkg
from physicore_collector.investigation_view import fmt

PKG = Path(pkg.__file__).parent


@pytest.mark.parametrize("n", [0, 1, 999_999, 1_000_000, 1_700_000_000_000_000_000, 2**53 + 1, 2**63 - 1])
def test_FM_01_offsets_are_exact_for_large_values(n):
    ms, rem = divmod(n, 1_000_000)
    assert fmt.offset_text(n) == f"+{ms}.{rem:06d} ms"
    assert Fraction(ms) + Fraction(rem, 10**6) == Fraction(n, 10**6)


def test_FM_02_negative_offsets_are_refused():
    with pytest.raises(ValueError):
        fmt.offset_text(-1)


@pytest.mark.parametrize("bad", [1.0, True, "1", None])
def test_FM_03_only_integers(bad):
    with pytest.raises(TypeError):
        fmt.ns(bad)
    with pytest.raises(TypeError):
        fmt.offset_text(bad)


def test_FM_04_interval_text():
    assert fmt.interval(5, 5) == "5 ns" and fmt.interval(5, 9) == "5 to 9 ns"


def test_FM_05_ns_beyond_2_53():
    assert fmt.ns(2**53 + 1) == "9007199254740993 ns"


@pytest.mark.parametrize("t,origin,span,width", [(0, 0, 10, 1000), (10, 0, 10, 1000), (5, 0, 10, 1000), (1, 0, 3, 1000), (2, 0, 3, 1000), (123456789012345678, 100, 2**60, 1000)])
def test_FM_06_x_pos_is_the_rounded_exact_quotient(t, origin, span, width):
    exact = Fraction((t - origin) * width, span)
    got = fmt.x_pos(t, origin, span, width)
    assert abs(Fraction(got) - exact) <= Fraction(1, 2)


def test_FM_07_x_pos_is_monotone_and_bounded():
    span = 997
    xs = [fmt.x_pos(t, 0, span, 1000) for t in range(span + 1)]
    assert xs == sorted(xs) and xs[0] == 0 and xs[-1] == 1000


def test_FM_08_zero_span_is_a_single_column():
    assert fmt.x_pos(0, 0, 0, 1000) == 0


def test_FM_09_plural():
    assert fmt.plural(1, "symptom") == "1 symptom" and fmt.plural(2, "symptom") == "2 symptoms" and fmt.plural(0, "x", "xes") == "0 xes"


def _modules():
    return [p for p in PKG.glob("*.py")]


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_FM_10_no_float_and_no_true_division_in_the_package(path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        assert not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)), f"{path.name}:{node.lineno} true division"
        assert not (isinstance(node, ast.Constant) and isinstance(node.value, float)), f"{path.name}:{node.lineno} float literal"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "float", f"{path.name}:{node.lineno} float()"
        assert not (isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Div))


def test_FM_11_rendered_output_has_no_float_text():
    import re
    from .fx import view_of
    from physicore_collector.investigation_view.markdown import render_markdown
    text = render_markdown(view_of())
    assert not re.search(r"\d\.\d+e[+-]?\d", text)
    assert not re.search(r"\d+\.\d+ ns", text)


def test_FM_12_all_time_fields_in_the_view_are_integers():
    from .fx import gen, view_of

    def walk(o, key=""):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, k)
        elif isinstance(o, list):
            for v in o:
                walk(v, key)
        elif key.endswith("_ns"):
            assert o is None or (isinstance(o, int) and not isinstance(o, bool)), (key, o)
        assert not isinstance(o, float)
    walk(view_of(gen(20, 3, width=300)))


def test_FM_13_offsets_in_a_lane_are_never_negative():
    from .fx import gen, view_of
    v = view_of(gen(50, 4, width=300))
    for ln in v["lanes"]:
        for it in ln["items"]:
            assert it["off_lower_ns"] >= 0 and it["off_upper_ns"] >= it["off_lower_ns"]
        assert ln["span_ns"] >= 0


@pytest.mark.parametrize("n,cut", [(0, False), (1, False), (19, False), (20, False), (21, True), (45, True)])
def test_FM_14_capjoin_boundary(n, cut):
    out = fmt.capjoin([str(i) for i in range(n)], ",")
    assert (" ... and " in out) == cut
    if n == 0:
        assert out == "none"
    elif cut:
        assert out == ",".join(str(i) for i in range(20)) + f" ... and {n - 20} more"
    else:
        assert out == ",".join(str(i) for i in range(n))


def test_FM_15_list_cap_is_twenty():
    assert fmt.LIST_CAP == 20
