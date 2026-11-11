"""Measure event_response cost (not a test).   python -m scripts.bench_m3b [--events N] [--per-event M]
Builds a synthetic recording of two topics, `/cmd` (String) and `/state` (String), with N-1 observed trigger events (the first block is idle) over N*M messages per topic;
runs the whole Investigator in a fresh subprocess per case; reports time and the child's own peak RSS (VmHWM). JSON payloads always; CDR payloads
when the `ros2` extra is installed (the CDR bytes here are written by hand for std_msgs/String, so building is fast).
Numbers describe THIS machine and THESE synthetic messages only."""
import argparse, io, json, os, platform, struct, subprocess, sys, tempfile, time
from pathlib import Path

MS = 1_000_000
BASE = 1_700_000_000_000_000_000
STRING_SCHEMA = b"string data\n"


def cdr_string(s: str) -> bytes:
    b = s.encode() + b"\x00"
    return b"\x00\x01\x00\x00" + struct.pack("<I", len(b)) + b


def build(path: Path, events: int, per_event: int, encoding: str) -> int:
    from mcap.writer import CompressionType, Writer
    out = io.BytesIO()
    w = Writer(out, chunk_size=1 << 20, compression=CompressionType.NONE, use_chunking=True)
    w.start(profile="", library="bench")
    if encoding == "cdr":
        sid = w.register_schema(name="std_msgs/msg/String", encoding="ros2msg", data=STRING_SCHEMA)
        menc = "cdr"
    else:
        sid = w.register_schema(name="synthetic/Sample", encoding="jsonschema", data=b'{"type":"object"}')
        menc = "json"
    ch = {t: w.register_channel(topic=t, message_encoding=menc, schema_id=sid) for t in ("/cmd", "/state")}
    n = events * per_event
    msgs = []
    for i in range(n):
        k, j = divmod(i, per_event)
        t = BASE + i * 10 * MS
        cmd = "STOP" if (j == 0 and k > 0) else "IDLE"  # one OBSERVED trigger entry per `per_event` samples (the first block starts idle, so every entry has a predecessor)
        state = "STOPPED" if j == 2 else "RUN"      # the response arrives 2 samples (20 ms) after the trigger
        for topic, v, dt in (("/cmd", cmd, 0), ("/state", state, MS)):  # /state is received 1 ms after /cmd: no equal timestamps across topics
            data = cdr_string(v) if encoding == "cdr" else json.dumps({"data": v}).encode()
            msgs.append((t + dt, ch[topic], data))
    for seq, (t, c, d) in enumerate(msgs):
        w.add_message(channel_id=c, log_time=t, publish_time=t, sequence=seq, data=d)
    w.finish()
    path.write_bytes(out.getvalue())
    return len(msgs)


def hwm_mb() -> float:
    for line in open("/proc/self/status"):
        if line.startswith("VmHWM:"):
            return round(int(line.split()[1]) / 1024, 1)
    return -1.0


def child(path: str, kind: str, deadline_ms: int) -> None:
    from physicore_collector.inventory import inspect_evidence
    from physicore_collector.investigator import investigate, parse_contract
    e = {"expectation_id": "B", "description": "bench", "topic": "/cmd", "signal": "data", "clock_domain": "mcap_log_time",
         "source": {"kind": "test_fixture", "reference": "scripts/bench_m3b.py"}}
    if kind == "event_response":
        e["check"] = {"kind": "event_response", "trigger_op": "eq", "trigger_value": "STOP",
                      "response": {"topic": "/state", "signal": "data", "op": "eq", "value": "STOPPED"}, "deadline_ns": deadline_ms * MS}
        e["applies_when"] = [{"topic": "/state", "signal": "data", "op": "ne", "value": "NEVER", "max_age_ns": 10**12}]
    else:
        e["check"] = {"kind": "equals", "value": "IDLE"}  # baseline: a per-sample check over the same trigger stream
    c = {"contract_schema_version": "1.0", "contract_id": "bench", "expectations": [e]}
    t0 = time.perf_counter()
    inv = inspect_evidence(path)
    t1 = time.perf_counter()
    r = investigate(inv, parse_contract(c))
    t2 = time.perf_counter()
    x = r.expectation_results[0]
    print(json.dumps({"kind": kind, "status": r.status.value, "inventory_s": round(t1 - t0, 3), "investigate_s": round(t2 - t1, 3),
                      "events": x.trigger_event_count, "matched": x.response_matched_count, "peak_rss_mb": hwm_mb()}))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=int, default=1000)
    ap.add_argument("--per-event", type=int, default=100)
    ap.add_argument("--child", nargs=3)
    a = ap.parse_args()
    if a.child:
        child(a.child[0], a.child[1], int(a.child[2]))
        return
    from physicore_collector.investigator.cdr import decoder_info
    info = decoder_info()
    out = {"python": platform.python_version(), "machine": platform.machine(), "decoder": f"{info.name} {info.version}" if info.available else "not installed",
           "events": a.events, "per_event": a.per_event, "results": []}
    with tempfile.TemporaryDirectory() as td:
        for enc in ("json", "cdr"):
            if enc == "cdr" and not info.available:
                continue
            p = Path(td) / f"{enc}.mcap"
            n = build(p, a.events, a.per_event, enc)
            for kind in ("baseline_equals", "event_response"):
                r = json.loads(subprocess.run([sys.executable, "-m", "scripts.bench_m3b", "--child", str(p), kind, "100"], capture_output=True, text=True, check=True).stdout)
                r.update(encoding=enc, messages=n, file_mb=round(p.stat().st_size / 1e6, 2))
                out["results"].append(r)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
