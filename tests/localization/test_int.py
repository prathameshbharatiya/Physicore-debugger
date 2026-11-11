"""INT-01..17 (TEST_PLAN 2). Seeded random properties; the seed is part of the failure message."""
import random

import pytest

from physicore_collector.localization.intervals import ClockDomainError, Interval
from tests.localization.oracle import oracle_order, oracle_precedes, oracle_separations

D = "mcap_log_time"
SEED = 20261103


def iv(a, b, dom=D):
    return Interval(dom, a, b)


def rand_iv(r, span=30):
    a = r.randint(0, span)
    return (a, a + r.randint(0, 8))


def test_INT_01_lower_above_upper_rejected():
    with pytest.raises(ValueError):
        iv(5, 4)


@pytest.mark.parametrize("bad", [1.0, True, "3", None, 2.5])
def test_INT_02_non_int_bounds_rejected(bad):
    with pytest.raises(TypeError):
        Interval(D, bad, 10)
    with pytest.raises(TypeError):
        Interval(D, 0, bad)


def test_INT_02_empty_domain_rejected():
    with pytest.raises(ValueError):
        Interval("", 1, 2)


@pytest.mark.parametrize("op", ["precedes", "order", "unordered_with", "min_separation", "max_separation"])
def test_INT_03_domain_mismatch_raises(op):
    with pytest.raises(ClockDomainError):
        getattr(iv(1, 2, "a"), op)(iv(3, 4, "b"))


def test_INT_04_strict_before():
    assert iv(1, 2).precedes(iv(3, 4)) and iv(1, 2).order(iv(3, 4)) == "before" and iv(3, 4).order(iv(1, 2)) == "after"


def test_INT_05_touching_is_unordered():
    assert not iv(1, 3).precedes(iv(3, 4)) and iv(1, 3).order(iv(3, 4)) == "unordered"


def test_INT_06_one_ns_gap_is_ordered():
    assert iv(1, 3).precedes(iv(4, 5))


def test_INT_07_equal_points_unordered():
    assert iv(3, 3).order(iv(3, 3)) == "unordered" and not iv(3, 3).precedes(iv(3, 3))


def test_INT_08_adjacent_points_ordered_one_way():
    assert iv(3, 3).precedes(iv(4, 4)) and not iv(4, 4).precedes(iv(3, 3))


def _triples(n=2000):
    r = random.Random(SEED)
    return [(rand_iv(r), rand_iv(r), rand_iv(r), rand_iv(r)) for _ in range(n)]


def test_INT_09_irreflexive():
    for a, *_ in _triples():
        assert not iv(*a).precedes(iv(*a)), f"seed={SEED} {a}"


def test_INT_10_asymmetric():
    for a, b, *_ in _triples():
        assert not (iv(*a).precedes(iv(*b)) and iv(*b).precedes(iv(*a))), f"seed={SEED} {a} {b}"


def test_INT_11_transitive():
    n = 0
    for a, b, c, _ in _triples(6000):
        if iv(*a).precedes(iv(*b)) and iv(*b).precedes(iv(*c)):
            n += 1
            assert iv(*a).precedes(iv(*c)), f"seed={SEED} {a} {b} {c}"
    assert n > 50          # the property was actually exercised


def test_INT_12_exactly_one_of_three_and_matches_oracle():
    for a, b, *_ in _triples():
        got = iv(*a).order(iv(*b))
        assert got == oracle_order(a, b), f"seed={SEED} {a} {b}"
        assert got in ("before", "after", "unordered")
        assert iv(*a).precedes(iv(*b)) == oracle_precedes(a, b)


def test_INT_13_interval_order_axiom():
    n = 0
    for x, y, z, w in _triples(6000):
        if iv(*x).precedes(iv(*y)) and iv(*z).precedes(iv(*w)):
            n += 1
            assert iv(*x).precedes(iv(*w)) or iv(*z).precedes(iv(*y)), f"seed={SEED} {x} {y} {z} {w}"
    assert n > 50


def test_INT_14_separations():
    assert iv(0, 10).min_separation(iv(30, 50)) == 20 and iv(0, 10).max_separation(iv(30, 50)) == 50


def test_INT_14_separations_match_oracle():
    for a, b, *_ in _triples(500):
        assert (iv(*a).min_separation(iv(*b)), iv(*a).max_separation(iv(*b))) == oracle_separations(a, b), f"seed={SEED} {a} {b}"


@pytest.mark.parametrize("lo,hi", [(2**63 - 1, 2**63 - 1), (2**63 + 1, 2**63 + 1), (2**64 - 1, 2**64 - 1), (0, 2**64 - 1)])
def test_INT_15_boundaries_exact(lo, hi):
    a, b = iv(0, 0), iv(lo, hi)
    assert type(a.min_separation(b)) is int and a.min_separation(b) == lo and a.max_separation(b) == hi
    assert a.precedes(b) == (lo > 0)
    assert iv(2**64 - 2, 2**64 - 2).precedes(iv(2**64 - 1, 2**64 - 1))


def test_INT_16_overlap_separation_not_positive():
    assert iv(0, 10).min_separation(iv(5, 6)) == -5 and iv(0, 10).order(iv(5, 6)) == "unordered"


def test_INT_17_time_shift_invariance():
    r = random.Random(SEED + 1)
    for _ in range(500):
        a, b = rand_iv(r), rand_iv(r)
        k = r.randint(-10**12, 10**12)
        sa, sb = (a[0] + k, a[1] + k), (b[0] + k, b[1] + k)
        assert iv(*a).order(iv(*b)) == iv(*sa).order(iv(*sb))
        assert iv(*a).min_separation(iv(*b)) == iv(*sa).min_separation(iv(*sb))
        assert iv(*a).max_separation(iv(*b)) == iv(*sa).max_separation(iv(*sb))


def test_width_and_point():
    assert iv(3, 9).width_ns == 6 and Interval.point(D, 5) == iv(5, 5)


@pytest.mark.parametrize("op", ["precedes", "order", "unordered_with", "min_separation", "max_separation"])
def test_INT_03_non_interval_operand_is_a_type_error(op):
    with pytest.raises(TypeError):
        getattr(iv(1, 2), op)(5)


def test_unordered_with_matches_order():
    assert iv(1, 3).unordered_with(iv(3, 4)) and not iv(1, 2).unordered_with(iv(3, 4)) and not iv(3, 4).unordered_with(iv(1, 2))
