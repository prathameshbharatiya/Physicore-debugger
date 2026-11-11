"""Benchmark of the M3d analysis (TEST_PLAN SCL-01, 02, 05, 06, 07; recorded, not tuned). Every scenario runs in a FRESH subprocess and reports wall
time, peak RSS (ru_maxrss, KiB) and RSS growth over the process baseline after the workload is built. Workloads are L1 symptoms (the analysis itself,
not the M3c detectors) plus a declared dependency graph.

usage: python -m tests.localization.tools.bench <scenario> <n>     scenarios: layers | overlap | bipartite_cap | fan"""
from __future__ import annotations

import json
import resource
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LANE = "mcap_log_time"


def _graph(edges):
    from physicore_collector.localization.dependencies import parse_dependencies
    nodes = sorted({e for ed in edges for e in ed[1:3]})
    doc = {"dependency_schema_version": 1, "declared_by": "bench", "nodes": [{"id": n} for n in nodes],
           "edges": [{"id": i, "from": a, "to": b, "relation": "data_flow", **({"window_ns": w} if w else {})} for i, a, b, w in edges]}
    r = parse_dependencies(json.dumps(doc).encode(), name="bench.json")
    assert r.graph is not None, r.problems[:3]
    return r.graph


def workload(scenario: str, n: int):
    from tests.localization.l1 import sym
    if scenario == "layers":                      # n symptoms on 5,000 nodes in a long declared chain; times increase along the chain
        nodes = 5000
        edges = [(f"e{i}", f"n{i}", f"n{i + 1}", 1_000_000) for i in range(nodes - 1)]
        syms = [sym(f"s{i:07d}", 1000 + i * 10, 1000 + i * 10, topic=f"n{(i * 7) % nodes}") for i in range(n)]
        return syms, _graph(edges)
    if scenario == "overlap":                     # everything overlaps: one giant undetermined layer
        edges = [(f"e{i}", f"n{i}", f"n{i + 1}", None) for i in range(99)]
        syms = [sym(f"s{i:07d}", 1000, 10**9, topic=f"n{i % 100}") for i in range(n)]
        return syms, _graph(edges)
    if scenario == "bipartite_cap":               # one declared edge a -> b with n/2 symptoms on each end: (n/2)^2 candidate links, the 200,000 cap is reached for n >= 900
        edges = [("e0", "a", "b", None)]
        syms = [sym(f"a{i:07d}", 1000 + i, 1000 + i, topic="a") for i in range(n // 2)] + [sym(f"b{i:07d}", 10**6 + i, 10**6 + i, topic="b") for i in range(n // 2)]
        return syms, _graph(edges)
    if scenario == "sparse":                      # 2,500 independent declared pairs a_k -> b_k (5,000 nodes); n symptoms spread evenly: links grow with n, below the cap up to ~40k
        k = 2500
        edges = [(f"e{i}", f"a{i}", f"b{i}", None) for i in range(k)]
        syms = [sym(f"s{i:07d}", 1000 + i, 1000 + i, topic=(f"a{(i // 2) % k}" if i % 2 == 0 else f"b{(i // 2) % k}")) for i in range(n)]
        return syms, _graph(edges)
    if scenario == "pairs":                       # n/4 independent declared pairs a_k -> b_k, two symptoms on each end: the number of links is LINEAR in n (4 per pair)
        k = n // 4
        edges = [(f"e{i}", f"a{i}", f"b{i}", None) for i in range(k)]
        syms = []
        for i in range(k):
            syms += [sym(f"a{i:06d}x", 1000 + 4 * i, 1000 + 4 * i, topic=f"a{i}"), sym(f"a{i:06d}y", 1001 + 4 * i, 1001 + 4 * i, topic=f"a{i}"),
                     sym(f"b{i:06d}x", 10**6 + 4 * i, 10**6 + 4 * i, topic=f"b{i}"), sym(f"b{i:06d}y", 10**6 + 1 + 4 * i, 10**6 + 1 + 4 * i, topic=f"b{i}")]
        return syms, _graph(edges)
    if scenario == "fan":                         # a root with 99 children, symptoms in time order
        edges = [(f"e{i}", "root", f"c{i}", None) for i in range(99)]
        syms = [sym(f"s{i:07d}", 1000 + i * 10, 1000 + i * 10, topic=("root" if i % 100 == 0 else f"c{i % 99}")) for i in range(n)]
        return syms, _graph(edges)
    raise SystemExit(f"unknown scenario {scenario}")


def run_one(scenario: str, n: int) -> dict:
    from physicore_collector.localization import chains as H
    from physicore_collector.localization import consistency as C
    from physicore_collector.localization.dependencies import WorkBudget
    from physicore_collector.localization.precedence import build_lanes
    syms, g = workload(scenario, n)
    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.perf_counter()
    budget = WorkBudget(1_000_000)
    res, bl = C.analyse_links(syms, g, None, coverage={}, budget=budget)
    ns = C.apply_assignments(syms, res.assignments)
    ana, bl2 = H.analyse_chains(ns, res.links, res, g, blockers=bl, budget=budget)
    lanes, _ = build_lanes(ns)
    dt = time.perf_counter() - t0
    rss1 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"scenario": scenario, "symptoms": n, "seconds": round(dt, 3), "peak_rss_kib": rss1, "rss_growth_kib": rss1 - rss0,
            "bytes_per_symptom": round((rss1 - rss0) * 1024 / max(1, n)), "links_reported": len(res.links), "links_total": res.links_total,
            "links_suppressed": res.links_suppressed, "links_status": res.links_status, "components": len(ana.components), "chains_reported": ana.chains_reported,
            "chains_total_status": ana.chains_total_status, "blockers": sorted({b.code for b in [*bl, *bl2]}), "lanes": len(lanes)}


def measure(scenario: str, n: int, timeout: int = 600) -> dict:
    import os
    p = subprocess.run([sys.executable, "-m", "tests.localization.tools.bench", scenario, str(n)], capture_output=True, text=True, cwd=ROOT, timeout=timeout,
                       env={**os.environ, "PYTHONPATH": f"{ROOT}/src:{ROOT}"})
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-2000:])
    return json.loads(p.stdout.strip().splitlines()[-1])


if __name__ == "__main__":
    print(json.dumps(run_one(sys.argv[1], int(sys.argv[2]))))
