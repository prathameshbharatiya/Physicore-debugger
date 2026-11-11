"""M4-0 scenario generator: seeded, deterministic synthetic recordings with a frozen truth record each (spec.json).

Every byte of every output derives from (family id, seed, spec.json). Nothing here is real-robot data. The hold-out split is only
generated when the caller asks for it explicitly (`allow_holdout=True`): it is not looked at while the catalogue is built.
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from tests import investigator_fixtures as F
from tests.anomaly import fixtures as X

CORPUS = Path(__file__).resolve().parent
SPEC_PATH = CORPUS / "spec.json"
SPEC = json.loads(SPEC_PATH.read_text())
SPEC_SHA = hashlib.sha256(SPEC_PATH.read_bytes()).hexdigest()
NS = 1_000_000_000
MS = 1_000_000
STEP = NS // SPEC["timing"]["rate_hz"]
N = SPEC["timing"]["messages"]
BASE = SPEC["timing"]["start_ns"]

F64 = "std_msgs/msg/Float64"
HDR = "std_msgs/msg/Header"
LOG = "rcl_interfaces/msg/Log"
DIAG = "diagnostic_msgs/msg/DiagnosticArray"
SCAN = "sensor_msgs/msg/LaserScan"
STR = "std_msgs/msg/String"
NAN = float("nan")


class ScenarioError(Exception):
    pass


def family(fid: str) -> dict:
    for f in SPEC["families"]:
        if f["family_id"] == fid:
            return f
    raise ScenarioError(f"unknown family {fid!r}")


def split_of(seed: int) -> str:
    if seed in SPEC["splits"]["dev"]["seeds"]:
        return "dev"
    if seed in SPEC["splits"]["holdout"]["seeds"]:
        return "holdout"
    raise ScenarioError(f"seed {seed} is in no split")


def namespace_of(seed: int) -> str:
    sp = split_of(seed)
    return SPEC["splits"]["dev"]["namespace"] if sp == "dev" else SPEC["splits"]["holdout"]["namespace_by_seed"][str(seed)]


def scenario_id(fid: str, seed: int) -> str:
    return f"{fid}-s{seed:03d}"


def scenario_ids(split: str, *, allow_holdout: bool = False) -> list[tuple[str, int]]:
    if split == "holdout" and not allow_holdout:
        raise PermissionError("the hold-out split is not generated unless allow_holdout=True is passed explicitly")
    if split not in ("dev", "holdout"):
        raise ScenarioError(f"unknown split {split!r}")
    return [(f["family_id"], s) for f in SPEC["families"] for s in SPEC["splits"][split]["seeds"]]


# ---------------------------------------------------------------------------------------------- payload builders
def t_of(i: int) -> int:
    return BASE + i * STEP


def stream_f64(rng, skip=range(0), nan_from=None, n=N):
    out = []
    for i in range(n):
        if i in skip:
            continue
        v = NAN if nan_from is not None and i >= nan_from else round(rng.uniform(-1, 1), 6)
        out.append((t_of(i), {"data": v}))
    return out


def stream_diag(error_at_ns=None, n_seconds=N // 10):
    out = [(BASE + s * NS, {"status": [{"level": 0, "name": "system", "hardware_id": "hw1", "message": "ok", "values": []}]}) for s in range(n_seconds)]
    if error_at_ns is not None:
        out.append((error_at_ns, {"status": [{"level": 2, "name": "system", "hardware_id": "hw1", "message": "input stale", "values": []}]}))
    return sorted(out, key=lambda x: x[0])


def stamp_payload(ns):
    return {"header": {"stamp": {"sec": ns // NS, "nanosec": ns % NS}, "frame_id": "odom"}}


def log_payload(level, msg):
    return {"level": level, "name": "tf_listener", "msg": msg}


# ---------------------------------------------------------------------------------------------- detector configs
LIMITS = {"max_retained_observations": 5, "max_text_bytes": 64, "max_findings_per_detector": 100}


def det_gap(topics):
    return {"tierB.publishing_gap": {"enabled": True, "topics": topics, "min_intervals": 10, "multiplier": "3", "absolute_floor_ns": 1}}


def det_nonfinite(topics):
    return {"tierB.non_finite": {"enabled": True, "topics": topics, "include_paths": [], "exclude_paths": []}}


def det_stamp(topics):
    return {"tierB.stamp_order": {"enabled": True, "topics": topics, "fields": ["header.stamp"], "partition_by": []}}


def det_rate(topics):
    return {"tierB.rate_change": {"enabled": True, "topics": topics, "window_ns": NS, "min_windows": 3, "min_baseline_count": 2, "trim_start_windows": 0,
                                  "trim_end_windows": 0, "low_ratio": "0.5", "high_ratio": "2"}}


def det_array(topic, path, length):
    return {"tierB.array_length": {"enabled": True, "rules": [{"topic": topic, "path": path, "expected_length": length}]}}


DIAG_ON = {"tierA.diagnostics": {"enabled": True}}
LOG_ON = {"tierA.ros_log": {"enabled": True}}


def er_contract(trigger_topic, trigger_value, resp_topic, resp_value, deadline_ms):
    check = {"kind": "event_response", "trigger_op": "eq", "trigger_value": trigger_value,
             "response": {"topic": resp_topic, "signal": "data", "op": "eq", "value": resp_value}, "deadline_ns": deadline_ms * MS}
    return F.contract([F.expectation("ER-STOP", topic=trigger_topic, signal="data", check=check)], contract_id="m4-corpus")


# ---------------------------------------------------------------------------------------------- the families
def _build(fid: str, seed: int, ns: str):
    """-> (streams {topic: [(t, payload)]}, types, detectors, edges [(from,to)], contract|None, faults [dict])"""
    rng = random.Random(f"m4-corpus:{fid}:{seed}")
    p = lambda t: ns + t  # noqa: E731
    lidar, imu, diag, odom = p("/lidar/scan"), p("/imu/data"), p("/diagnostics"), p("/odom")
    streams, types, edges, contract, faults = {}, {}, [], None, []

    def add(topic, msgs, typ):
        streams[topic], types[topic] = msgs, typ

    if fid in ("F01", "F07"):
        k, length = rng.randint(30, 50), rng.randint(20, 30)
        add(lidar, stream_f64(rng, skip=range(k, k + length)), F64)
        last_before = t_of(k - 1)
        add(diag, stream_diag(error_at_ns=last_before + 300 * MS), DIAG)
        det = {**det_gap([lidar]), **DIAG_ON}
        edges = [(lidar, diag)]
        faults = [{"fault_id": "a", "true_class": "upstream_source_silent", "topic": lidar, "onset_ns": last_before, "end_ns": t_of(k + length)}]
        if fid == "F07":
            h = p("/rosout")
            add(h, [(t_of(k - 15) + j * 200 * MS, log_payload(30, "transform lookup retried")) for j in range(5)], LOG)
            det = {**det, **LOG_ON}
    elif fid == "F02":
        k = rng.randint(30, 60)
        add(imu, stream_f64(rng, nan_from=k), F64)
        add(diag, stream_diag(error_at_ns=t_of(k) + 200 * MS), DIAG)
        det = {**det_nonfinite([imu]), **DIAG_ON}
        edges = [(imu, diag)]
        faults = [{"fault_id": "a", "true_class": "invalid_sensor_data_propagated", "topic": imu, "onset_ns": t_of(k), "end_ns": None}]
    elif fid == "F03":
        c = rng.randint(40, 60)
        cmd, st = p("/cmd_mode"), p("/robot_state")
        add(cmd, [(BASE, {"data": "RUN"}), (t_of(c), {"data": "STOP"})], STR)
        add(st, [(t_of(i), {"data": "RUNNING"}) for i in range(N)], STR)
        add(diag, stream_diag(), DIAG)
        det = {**DIAG_ON}
        edges = [(cmd, st)]
        contract = er_contract(cmd, "STOP", st, "STOPPED", 300)
        faults = [{"fault_id": "a", "true_class": "command_not_followed", "topic": st, "onset_ns": t_of(c), "end_ns": None}]
    elif fid == "F04":
        k = rng.randint(30, 60)
        scan = p("/scan")
        add(scan, [(t_of(i), {"header": {"stamp": {"sec": 0, "nanosec": 0}, "frame_id": "laser"},
                              "ranges": [round(rng.uniform(0.1, 9.0), 3) for _ in range(359 if i >= k else 360)]}) for i in range(N)], SCAN)
        det = det_array(scan, "ranges", 360)
        faults = [{"fault_id": "a", "true_class": "configuration_mismatch", "topic": scan, "onset_ns": t_of(k), "end_ns": None}]
    elif fid == "F05":
        k = rng.randint(30, 60)
        add(odom, [(t_of(i), stamp_payload(t_of(i) - (5 * NS if i >= k else 0))) for i in range(N)], HDR)
        det = det_stamp([odom])
        faults = [{"fault_id": "a", "true_class": "timestamp_or_clock_fault", "topic": odom, "onset_ns": t_of(k), "end_ns": None}]
    elif fid == "F06":
        k, length = rng.randint(15, 25), rng.randint(20, 25)
        k2 = rng.randint(65, 80)
        add(lidar, stream_f64(rng, skip=range(k, k + length)), F64)
        add(imu, stream_f64(rng, nan_from=k2), F64)
        det = {**det_gap([lidar]), **det_nonfinite([imu])}
        faults = [{"fault_id": "a", "true_class": "upstream_source_silent", "topic": lidar, "onset_ns": t_of(k - 1), "end_ns": t_of(k + length)},
                  {"fault_id": "b", "true_class": "invalid_sensor_data_propagated", "topic": imu, "onset_ns": t_of(k2), "end_ns": None}]
    elif fid == "F08":
        k = rng.randint(30, 45)
        a, b = p("/sensor_a"), p("/sensor_b")
        add(a, stream_f64(rng, skip=range(k + 5, k + 25)), F64)
        add(b, stream_f64(rng, skip=range(k, k + 20)), F64)
        det = det_gap([a, b])
        edges = [(a, b)]
        faults = [{"fault_id": "a", "true_class": "upstream_source_silent", "topic": b, "onset_ns": t_of(k - 1), "end_ns": t_of(k + 20)}]
    elif fid == "F09":
        add(lidar, stream_f64(rng), F64)
        add(imu, stream_f64(rng), F64)
        add(diag, stream_diag(), DIAG)
        det = {**det_gap([lidar, imu]), **det_nonfinite([lidar, imu]), **DIAG_ON}
        edges = [(lidar, diag)]
    elif fid == "F10":
        k = rng.randint(62, 70)     # late enough that the degraded windows stay a minority: the rate detector's baseline is a median
        msgs = [(t_of(i), {"data": round(rng.uniform(-1, 1), 6)}) for i in range(k)]
        t = t_of(k)
        end = t_of(N)
        while t < end:
            msgs.append((t, {"data": round(rng.uniform(-1, 1), 6)}))
            t += 250 * MS
        add(odom, msgs, F64)
        det = det_rate([odom])
        faults = [{"fault_id": "a", "true_class": "hardware_degradation", "topic": odom, "onset_ns": t_of(k), "end_ns": None}]
    else:
        raise ScenarioError(f"family {fid!r} has no builder")
    return streams, types, det, edges, contract, faults


def _links(pairs, ns):
    return [[ns + a, ns + b] for a, b in pairs]


def _dump(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def generate(fid: str, seed: int, out_root: Path, *, allow_holdout: bool = False) -> Path:
    """Write one scenario directory and return it. Same inputs, same bytes."""
    fam = family(fid)
    sp = split_of(seed)
    if sp == "holdout" and not allow_holdout:
        raise PermissionError("the hold-out split is not generated unless allow_holdout=True is passed explicitly")
    ns = namespace_of(seed)
    streams, types, det, edges, contract, faults = _build(fid, seed, ns)
    sid = scenario_id(fid, seed)
    root = Path(out_root) / sid
    root.mkdir(parents=True)
    X.write_typed_bag(root / "bag", [streams], types)
    (root / "anomaly.json").write_text(_dump({"anomaly_config_version": 1, "limits": LIMITS, "detectors": det}))
    if edges:
        lines = ["dependency_schema_version: 1", 'declared_by: "m4 corpus generator (synthetic)"', "edges:"]
        lines += [f'  - {{id: e{i + 1}, from: {a}, to: {b}, relation: data_flow, window_ns: {NS}}}' for i, (a, b) in enumerate(edges)]
        (root / "dependencies.yaml").write_text("\n".join(lines) + "\n")
    if contract is not None:
        (root / "expectations.json").write_text(_dump(contract))
    truth = {
        "scenario_id": sid, "family_id": fid, "family_name": fam["name"], "seed": seed, "split": sp, "namespace": ns, "spec_sha256": SPEC_SHA,
        "answer_kind": fam["answer_kind"], "faults": faults, "acceptable_leaders": fam["acceptable_leaders"],
        "forbidden_leader_classes": fam["forbidden_leader_classes"], "forbidden_links": _links(fam["forbidden_links"], ns),
        "candidates_expected_unassessable": fam["candidates_expected_unassessable"], "required_missing_evidence": fam["required_missing_evidence"],
        "expected_detectors": fam["expected_detectors"], "needs_contract": fam["needs_contract"],
        "declared_edges": [[a, b] for a, b in edges],
    }
    (root / "truth.json").write_text(_dump(truth))
    (root / "FILES.sha256").write_text("".join(f"{h}  {n}\n" for n, h in sorted(file_hashes(root, skip={"FILES.sha256"}).items())))
    return root


def file_hashes(root: Path, skip=frozenset()) -> dict[str, str]:
    root = Path(root)
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file() and p.name not in skip}


def generate_split(split: str, out_root: Path, *, allow_holdout: bool = False) -> list[Path]:
    return [generate(f, s, out_root, allow_holdout=allow_holdout) for f, s in scenario_ids(split, allow_holdout=allow_holdout)]


def corpus_digest(out_root: Path) -> str:
    """One hash over every file of every scenario directory below out_root (names and bytes)."""
    h = hashlib.sha256()
    for n, fh in sorted(file_hashes(out_root).items()):
        h.update(f"{n}\0{fh}\n".encode())
    return h.hexdigest()
