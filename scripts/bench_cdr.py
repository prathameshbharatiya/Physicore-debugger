"""Fixture building (rosbags serialization) is the slow part: ~8 min for 3x20000 messages here; decoding itself takes seconds.
Measure CDR decoding throughput and memory (not a test).  python -m scripts.bench_cdr [--messages N]
Needs extras `ros2` and `xcheck` (the second only to build the synthetic recordings).  Each case runs in a fresh subprocess so peak RSS is
that case's own.  Numbers are for THIS machine and THESE synthetic messages; they are not a claim about real robot recordings."""
import argparse, json, os, platform, resource, subprocess, sys, tempfile, time
from pathlib import Path

CASES = ("float64", "joint_state7", "odometry")


def build(case, n, path):
    from tests import cdr_fixtures as cf
    ts = cf.typestore()
    if case == "float64":
        ch = ("/x", "std_msgs/msg/Float64", [(cf.t(i, 1), cf.float64(i * 0.5)) for i in range(n)])
    elif case == "joint_state7":
        names = [f"joint_{k}" for k in range(7)]
        ch = ("/x", "sensor_msgs/msg/JointState", [(cf.t(i, 1), cf.joint_state(cf.t(i, 1), names, [i * 0.001 + k for k in range(7)], [0.1] * 7, [1.0] * 7)) for i in range(n)])
    else:
        ch = ("/x", "nav_msgs/msg/Odometry", [(cf.t(i, 1), cf.odometry(cf.t(i, 1), i * 0.01, 0.0, 0.5, 0.1)) for i in range(n)])
    cf.write_cdr_mcap(path, [ch], chunk_size=1 << 20)


def hwm_mb():
    """This process's own peak resident set (VmHWM): ru_maxrss can inherit the parent's peak across fork/exec."""
    for line in open("/proc/self/status"):
        if line.startswith("VmHWM:"):
            return round(int(line.split()[1]) / 1024, 1)


def measure(case, path, mode):
    from physicore_collector.inventory import inspect_evidence
    from physicore_collector.investigator import investigate, parse_contract
    from physicore_collector.investigator.decoding import decode_topic
    from tests.investigator_fixtures import contract, expectation
    inv = inspect_evidence(path)
    sig = {"float64": "data", "joint_state7": "position.3", "odometry": "pose.pose.position.x"}[case]
    t0 = time.perf_counter()
    if mode == "decode_all":  # adapter only: every message decoded, one leaf read
        from physicore_collector.investigator import observations as ob
        ev = ob.load_evidence(inv, parse_contract(contract([expectation(topic="/x", signal=sig)])))
        n = sum(1 for _ in ev.topic_codecs) and next(iter(ev.topic_codecs.values())).decoded
    else:  # whole Investigator, with a check that never fails
        r = investigate(inv, parse_contract(contract([expectation(topic="/x", signal=sig, check={"kind": "range", "max": 1e12})])))
        n = r.evidence.payload_decoding.topics[0].messages_decoded
    dt = time.perf_counter() - t0
    return {"case": case, "mode": mode, "messages_decoded": n, "seconds": round(dt, 3), "msg_per_s": round(n / dt) if dt else None,
            "peak_rss_mb": hwm_mb(), "file_mb": round(os.path.getsize(path) / 1e6, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--messages", type=int, default=50000)
    ap.add_argument("--child", nargs=3)
    a = ap.parse_args()
    if a.child:
        print(json.dumps(measure(*a.child)))
        return
    from physicore_collector.investigator.cdr import decoder_info
    d = decoder_info()
    out = {"python": platform.python_version(), "machine": platform.machine(), "decoder": f"{d.name} {d.version}", "messages": a.messages, "results": []}
    with tempfile.TemporaryDirectory() as td:
        for case in CASES:
            p = Path(td) / f"{case}.mcap"
            build(case, a.messages, p)
            for mode in ("investigate",):
                base = subprocess.run([sys.executable, "-c", "from scripts.bench_cdr import hwm_mb;import physicore_collector.investigator,tests.investigator_fixtures;print(hwm_mb())"], capture_output=True, text=True).stdout.strip()
                r = json.loads(subprocess.run([sys.executable, "-m", "scripts.bench_cdr", "--child", case, str(p), mode], capture_output=True, text=True, check=True).stdout)
                r["baseline_import_rss_mb"] = round(float(base), 1)
                out["results"].append(r)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
