"""Independent replay verifier for M3d reports (TEST_PLAN PRV-01, 04, 09, 11, 15, 16; test-only).

Works on the report's JSON (a dict) and the raw artifact FILES, with the plain `mcap` reader. It imports nothing from `physicore_collector.localization`
(or from the accepted loaders). For one report it checks:
  * every evidence digest of the report equals the sha256 recomputed from the file;
  * every symptom evidence reference resolves: sha256 equal, a message at `message_index` (position in file order), the same topic and `log_time`;
  * every symptom onset in the log-time domain is recomputed from the raw messages: `received_message` = the log_time of a referenced message,
    `silence_between_captured_messages` = the log_times of two referenced messages (lower, upper); other bases are counted as `not_replayed`
    (reported, never silently passed);
  * every blocker: each artifact digest equals the recomputed digest; every observation reference exists in the file (index, topic, log_time) and a
    metadata-only blocker carries no observation reference.
Returns `Replay(problems, checked)`; an empty `problems` list means everything that could be replayed replayed."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from mcap.reader import make_reader

LOG = "mcap_log_time"


@dataclass
class Replay:
    problems: list[str] = field(default_factory=list)
    checked: dict[str, int] = field(default_factory=lambda: {"evidence_digests": 0, "symptom_refs": 0, "onsets": 0, "onsets_not_replayed": 0,
                                                              "blocker_artifacts": 0, "blocker_observations": 0})

    @property
    def ok(self) -> bool:
        return not self.problems


class _Files:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._cache: dict[str, tuple[str, list]] = {}

    def get(self, ref: str):
        if ref not in self._cache:
            p = self.root / ref.removeprefix("artifact:")
            data = p.read_bytes()
            with open(p, "rb") as fh:
                rows = [(c.topic, m.log_time) for _s, c, m in make_reader(fh).iter_messages(log_time_order=False)]
            self._cache[ref] = (hashlib.sha256(data).hexdigest(), rows)
        return self._cache[ref]


def verify(report: dict, root: Path) -> Replay:
    out = Replay()
    files = _Files(root)

    def msg(ref: str, idx: int, where: str):
        try:
            sha, rows = files.get(ref)
        except OSError as e:
            out.problems.append(f"{where}: cannot re-read the artifact: {e}")
            return None
        if not 0 <= idx < len(rows):
            out.problems.append(f"{where}: no message at index {idx}")
            return None
        return sha, rows[idx]

    for e in report.get("evidence", []):
        try:
            sha, _ = files.get(e["artifact_ref"])
        except OSError as ex:
            out.problems.append(f"evidence {e['artifact_ref']}: cannot re-read: {ex}")
            continue
        out.checked["evidence_digests"] += 1
        if sha != e["sha256"]:
            out.problems.append(f"evidence {e['artifact_ref']}: file sha256 {sha[:12]} != report {e['sha256'][:12]}")

    for s in report.get("symptoms", []):
        times: list[int] = []
        for ev in s.get("evidence", []):
            where = f"{s['symptom_id']} {ev['artifact_ref']}#{ev['message_index']}"
            got = msg(ev["artifact_ref"], ev["message_index"], where)
            if got is None:
                continue
            sha, (topic, log_time) = got
            out.checked["symptom_refs"] += 1
            if ev.get("sha256") is not None and sha != ev["sha256"]:
                out.problems.append(f"{where}: file sha256 differs from the reference")
            if topic != ev["topic"]:
                out.problems.append(f"{where}: topic {topic} != {ev['topic']}")
            if ev.get("clock_domain") in (None, LOG) and ev.get("time_ns") is not None and log_time != ev["time_ns"]:
                out.problems.append(f"{where}: log_time {log_time} != {ev['time_ns']}")
            times.append(log_time)
        placements = [(s["onset"], s["onset_basis"])] + [(p["onset"], p["onset_basis"]) for p in s.get("secondary_onsets", [])]
        for iv, basis in placements:
            if iv["domain"] != LOG:
                out.checked["onsets_not_replayed"] += 1
                continue
            if basis == "received_message":
                out.checked["onsets"] += 1
                if not (iv["lower_ns"] == iv["upper_ns"] and iv["lower_ns"] in times):
                    out.problems.append(f"{s['symptom_id']}: onset {iv['lower_ns']} is not the log_time of a referenced message")
            elif basis == "silence_between_captured_messages":
                out.checked["onsets"] += 1
                if not (iv["lower_ns"] in times and iv["upper_ns"] in times and iv["lower_ns"] < iv["upper_ns"]):
                    out.problems.append(f"{s['symptom_id']}: silence bounds {iv['lower_ns']}..{iv['upper_ns']} are not log_times of referenced messages")
            else:
                out.checked["onsets_not_replayed"] += 1

    for b in report.get("blockers", []):
        for a in b.get("artifacts", []):
            out.checked["blocker_artifacts"] += 1
            try:
                sha, _ = files.get(a["artifact_ref"])
            except OSError as ex:
                out.problems.append(f"{b['blocker_id']}: artifact {a['artifact_ref']} cannot be re-read: {ex}")
                continue
            if a.get("sha256") is not None and sha != a["sha256"]:
                out.problems.append(f"{b['blocker_id']}: artifact digest for {a['artifact_ref']} differs from the file")
        for o in b.get("observations", []):
            where = f"{b['blocker_id']} {o['artifact_ref']}#{o['message_index']}"
            got = msg(o["artifact_ref"], o["message_index"], where)
            if got is None:
                continue
            out.checked["blocker_observations"] += 1
            sha, (topic, log_time) = got
            if o.get("topic") is not None and topic != o["topic"]:
                out.problems.append(f"{where}: topic {topic} != {o['topic']}")
            if o.get("time_ns") is not None and o.get("clock_domain") in (None, LOG) and log_time != o["time_ns"]:
                out.problems.append(f"{where}: log_time {log_time} != {o['time_ns']}")
    return out
