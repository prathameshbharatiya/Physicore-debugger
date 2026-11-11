"""Independent naive oracle (TEST_PLAN 1): written from DESIGN 4 with no shared code. Brute force over the instants an interval contains,
so it is only usable on small integer ranges; larger cases are checked by arithmetic assertions in the tests."""
from __future__ import annotations


def instants(lo: int, hi: int) -> range:
    return range(lo, hi + 1)


def oracle_precedes(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """True iff every instant of a is strictly earlier than every instant of b."""
    return all(x < y for x in instants(*a) for y in instants(*b))


def oracle_order(a: tuple[int, int], b: tuple[int, int]) -> str:
    if oracle_precedes(a, b):
        return "before"
    if oracle_precedes(b, a):
        return "after"
    return "unordered"


def oracle_separations(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
    diffs = [y - x for x in instants(*a) for y in instants(*b)]
    return min(diffs), max(diffs)


def oracle_layers(intervals: dict[str, tuple[int, int]]) -> dict[str, int]:
    """Naive fixed-point relaxation: layer(x) = max(layer(y) + 1 for every y with y.upper < x.lower), 0 if none. O(n^3) worst case; small inputs only."""
    layer = {k: 0 for k in intervals}
    for _ in range(len(intervals) + 1):
        changed = False
        for x, (xl, xu) in intervals.items():
            best = max((layer[y] + 1 for y, (yl, yu) in intervals.items() if y != x and yu < xl), default=0)
            if best != layer[x]:
                layer[x] = best
                changed = True
        if not changed:
            break
    return layer


# ---------------------------------------------------------------------------------------------- Step 6: declared-dependency consistency (DESIGN 4.4)

def oracle_label(x: tuple[int, int], y: tuple[int, int], window):
    """Label of a link with upstream symptom interval x and downstream y, decided on the set of possible instant differences (y - x)."""
    diffs = [b - a for a in instants(*x) for b in instants(*y)]
    if all(d > 0 for d in diffs):
        return "consistent_with_propagation" if window is None or min(diffs) <= window else "outside_declared_window"
    if all(d < 0 for d in diffs):
        return "inconsistent_order"
    return "undetermined"


def oracle_simple_paths(edges: list[tuple[str, str, str, object]], src: str, dst: str) -> list[tuple[str, ...]]:
    """Every simple directed path src -> dst as a tuple of edge ids (plain recursion; tiny graphs only)."""
    out: list[tuple[str, ...]] = []

    def go(node, seen, trail):
        for eid, a, b, _w in edges:
            if a == node and b not in seen:
                if b == dst:
                    out.append(tuple(trail + [eid]))
                else:
                    go(b, seen | {b}, trail + [eid])
    if src != dst:
        go(src, {src}, [])
    return sorted(out)


def oracle_links(symptoms: dict[str, tuple[str, str, tuple[int, int]]], edges: list[tuple[str, str, str, object]]) -> set[tuple]:
    """symptoms: id -> (node, lane, (lo, hi)); one lane per symptom. -> {(x, y, lane, edge ids, label, sep_min, sep_max)} for every declared path."""
    out = set()
    win = {e[0]: e[3] for e in edges}
    for xi, (xn, xl, xv) in symptoms.items():
        for yi, (yn, yl, yv) in symptoms.items():
            if xi == yi or xl != yl or xn == yn:
                continue
            for path in oracle_simple_paths(edges, xn, yn):
                ws = [win[e] for e in path]
                w = None if any(v is None for v in ws) else sum(ws)
                diffs = [b - a for a in instants(*xv) for b in instants(*yv)]
                out.add((xi, yi, xl, path, oracle_label(xv, yv, w), min(diffs), max(diffs)))
    return out


def oracle_no_declared(symptoms: dict[str, tuple[str, str, tuple[int, int]]], edges) -> int:
    """Same-lane unordered pairs on different nodes with no declared path in either direction."""
    n = 0
    ids = sorted(symptoms)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            (an, al, _), (bn, bl, _) = symptoms[a], symptoms[b]
            if al == bl and an != bn and not oracle_simple_paths(edges, an, bn) and not oracle_simple_paths(edges, bn, an):
                n += 1
    return n


# ---------------------------------------------------------------------------------------------- Step 7: maximal paths of a DAG (recursive, tiny graphs only)

def oracle_maximal_paths(arcs: set[tuple[str, str]], key: dict[str, tuple]) -> list[tuple[str, ...]]:
    """Every maximal path (source to sink) of the DAG given by `arcs`, sorted by the sequence of vertex keys (the specified canonical order)."""
    succ: dict[str, list[str]] = {}
    indeg: dict[str, int] = {}
    for a, b in arcs:
        succ.setdefault(a, []).append(b)
        indeg[b] = indeg.get(b, 0) + 1
    verts = {v for ab in arcs for v in ab}
    out: list[tuple[str, ...]] = []

    def go(v, trail):
        if v not in succ:
            out.append(tuple(trail))
            return
        for w in succ[v]:
            go(w, trail + [w])
    for v in verts:
        if indeg.get(v, 0) == 0:
            go(v, [v])
    return sorted(out, key=lambda p: [key[v] for v in p])
