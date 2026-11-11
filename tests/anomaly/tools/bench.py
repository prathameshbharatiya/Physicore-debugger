"""Benchmark (TEST_PLAN X-12/X-13; recorded, not gated). Builds a rosbag2 directory of N messages with a streaming MCAP writer, then measures each
scenario in a FRESH subprocess: peak RSS (ru_maxrss) and wall time.  usage: python -m tests.anomaly.tools.bench N [json|cdr]  (cdr needs the ros2 venv)"""
from __future__ import annotations

import json
import os
import resource
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = 1_700_000_000_000_000_000
STEP = 10_000_000


def build(root: Path, n: int, kind: str) -> Path:
    from mcap.writer import CompressionType, Writer
    from tests import make_fixtures as mf
    from tests.anomaly.fixtures import _write_meta
    root.mkdir(parents=True)
    path = root / f"{root.name}_0.mcap"
    with open(path, "wb") as fh:
        w = Writer(fh, chunk_size=1 << 20, compression=CompressionType.NONE)
        w.start(profile="", library=mf.LIBRARY)
        if kind == "json":
            sid = w.register_schema(name="std_msgs/msg/Float64", encoding="jsonschema", data=b'{"type":"object"}')
            cid = w.register_channel(topic="/t", message_encoding="json", schema_id=sid)
        else:
            from tests import cdr_fixtures as cf
            sid = w.register_schema(name="std_msgs/msg/Float64", encoding="ros2msg", data=cf.schema_text("std_msgs/msg/Float64"))
            cid = w.register_channel(topic="/t", message_encoding="cdr", schema_id=sid)
        times = []
        for i in range(n):
            t = BASE + i * STEP + (STEP * 40 if i >= n // 2 else 0)
            times.append(t)
            v = float("nan") if i % 1000 == 7 else 1.5
            data = json.dumps({"data": v}).encode() if kind == "json" else b"\x00\x01\x00\x00" + struct.pack("<d", v)
            w.add_message(channel_id=cid, log_time=t, publish_time=t, sequence=i, data=data)
        w.finish()
    _write_meta(root, [(path.name, [mf.StreamSpec("/t", tuple(times))])])
    return root


SCENARIOS = {
    "load_only": {},
    "gap_rate": {"tierB.publishing_gap": {"enabled": True, "topics": ["/t"], "min_intervals": 10, "multiplier": "3", "absolute_floor_ns": 1},
                 "tierB.rate_change": {"enabled": True, "topics": ["/t"], "window_ns": 1_000_000_000, "min_windows": 3, "min_baseline_count": 2,
                                       "trim_start_windows": 0, "trim_end_windows": 0, "low_ratio": "0.5", "high_ratio": "2"}},
    "non_finite": {"tierB.non_finite": {"enabled": True, "topics": ["/t"], "include_paths": [], "exclude_paths": []}},
    "stamp_order": {"tierB.stamp_order": {"enabled": True, "topics": ["/t"], "fields": ["data"], "partition_by": []}},
}


def child(bag: str, scenario: str) -> None:
    from physicore_collector.anomaly.config import validate_config
    from physicore_collector.anomaly.runner import run, topic_schema_names, wanted_topics
    from physicore_collector.anomaly.service import all_detectors
    from physicore_collector.inventory import inspect_evidence
    from physicore_collector.investigator.observations import load_evidence
    t0 = time.perf_counter()
    inv = inspect_evidence(bag)
    detectors = SCENARIOS[scenario]
    lc = validate_config({"anomaly_config_version": 1, "limits": {"max_retained_observations": 20, "max_text_bytes": 512, "max_findings_per_detector": 1000},
                          "detectors": detectors})
    assert lc.config is not None, lc.problems
    ds = all_detectors()
    topics = wanted_topics(ds, topic_schema_names(inv), lc.config) if detectors else {"/t"}
    ev = load_evidence(inv, topics)
    t1 = time.perf_counter()
    rss_loaded = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    out = run(inv, ev, lc.config, ds, track_decodes=False) if detectors else None
    t2 = time.perf_counter()
    print(json.dumps({"scenario": scenario, "messages": sum(len(v) for v in ev.messages.values()), "complete": ev.complete,
                      "findings": len(out.findings) if out else 0, "decoded": out.decode_counter.total if out else 0,
                      "seconds_inspect_load": round(t1 - t0, 2), "seconds_detect": round(t2 - t1, 2),
                      "peak_rss_mb_after_load": round(rss_loaded / 1024, 1), "peak_rss_mb_final": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)}))


def main(argv: list[str]) -> None:
    if argv and argv[0] == "--child":
        child(argv[1], argv[2])
        return
    n, kind = int(argv[0]), (argv[1] if len(argv) > 1 else "json")
    with tempfile.TemporaryDirectory() as td:
        t0 = time.perf_counter()
        bag = build(Path(td) / "bag", n, kind)
        print(f"# built {n} {kind} messages in {time.perf_counter() - t0:.1f}s, {sum(p.stat().st_size for p in bag.iterdir()) / 1e6:.1f} MB", flush=True)
        for sc in SCENARIOS:
            p = subprocess.run([sys.executable, "-m", "tests.anomaly.tools.bench", "--child", str(bag), sc], cwd=ROOT, capture_output=True, text=True,
                               env={**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT)})
            print(p.stdout.strip() or p.stderr.strip()[-400:], flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
