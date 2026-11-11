"""Verification of evidence references against the recording (FR-VR-*, read-only).

For every artifact the report lists, the SHA-256 of the file is recomputed; for every symptom or blocker reference the message at the stated position
(file order, as M3c and M3d number them) must have the stated topic and, in the log-time domain, the stated time. Results are exact strings:
`verified`, `mismatch` (with a reason) or `not_checked` (with a reason). Nothing is written; a missing, replaced or damaged artifact never crashes."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .safety import clean_text

LOG = "mcap_log_time"


def _resolve(root: Path, ref: str) -> tuple[Path | None, str]:
    if not ref.startswith("artifact:"):
        return None, "the reference is not an artifact reference"
    rel = ref[len("artifact:"):]
    parts = Path(rel).parts
    if not rel or Path(rel).is_absolute() or ".." in parts:
        return None, "the reference leaves the evidence location"
    if root.is_file():
        return (root, "") if rel == root.name or parts[-1] == root.name and len(parts) == 1 else (None, "the evidence location is a single file with another name")
    p = root.joinpath(rel)
    try:
        p.resolve().relative_to(root.resolve())
    except ValueError:
        return None, "the reference leaves the evidence location"
    return (p, "") if p.is_file() else (None, "the artifact is not present at the evidence location")


def _scan(p: Path, wanted: set[int]) -> tuple[str, dict[int, tuple[str, int]], str]:
    """(sha256, {index: (topic, log_time)} for the wanted indexes, error). The digest is computed even when the container cannot be read."""
    h = hashlib.sha256()
    try:
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError as e:
        return "", {}, f"cannot read the artifact: {e.strerror or e}"
    rows: dict[int, tuple[str, int]] = {}
    err = ""
    if wanted:
        try:
            from mcap.reader import make_reader
            hi = max(wanted)
            with open(p, "rb") as fh:
                for i, (_s, c, m) in enumerate(make_reader(fh).iter_messages(log_time_order=False)):
                    if i in wanted:
                        rows[i] = (c.topic, m.log_time)
                    if i >= hi:
                        break
        except Exception as e:      # a damaged container is reported, never raised
            err = f"cannot read the messages: {type(e).__name__}"
    return h.hexdigest(), rows, err


def verify_view(view: dict, path: str | Path) -> dict:
    root = Path(path)
    listed = {e["artifact_ref"] for e in view["header"]["evidence"]}
    # the references are cleaned display text; the originals are only needed for lookup, and the cleaned form equals the original for every real reference
    wanted: dict[str, set[int]] = {}
    for r in view["evidence_refs"]:
        wanted.setdefault(r["artifact_ref"], set()).add(r["message_index"])
    for ref in listed:
        wanted.setdefault(ref, set())
    scans: dict[str, tuple] = {}
    for ref, idx in wanted.items():
        p, why = _resolve(root, ref)
        scans[ref] = (None, why) if p is None else (_scan(p, idx), "")

    def status(ref: str):
        sc, why = scans[ref]
        return sc, why

    artifacts = {}
    for e in view["header"]["evidence"]:
        sc, why = status(e["artifact_ref"])
        if sc is None:
            artifacts[e["artifact_ref"]] = {"status": "not_checked", "detail": why}
        elif not sc[0]:
            artifacts[e["artifact_ref"]] = {"status": "not_checked", "detail": sc[2]}
        elif sc[0] != e["sha256"]:
            artifacts[e["artifact_ref"]] = {"status": "mismatch", "detail": "the SHA-256 of the file differs from the report"}
        else:
            artifacts[e["artifact_ref"]] = {"status": "verified", "detail": ""}

    refs = {}
    for r in view["evidence_refs"]:
        ref = r["artifact_ref"]
        sc, why = status(ref)
        if ref not in listed:
            refs[r["id"]] = {"status": "not_checked", "detail": "the artifact is not listed in the evidence of the report"}
        elif sc is None:
            refs[r["id"]] = {"status": "not_checked", "detail": why}
        elif not sc[0]:
            refs[r["id"]] = {"status": "not_checked", "detail": sc[2]}
        elif r["sha256"] and sc[0] != r["sha256"]:
            refs[r["id"]] = {"status": "mismatch", "detail": "the SHA-256 of the file differs from the reference"}
        else:
            got = sc[1].get(r["message_index"])
            if got is None:
                refs[r["id"]] = {"status": "not_checked" if sc[2] else "mismatch", "detail": sc[2] or "there is no message at this position"}
            elif r["topic"] is not None and got[0] != r["topic"]:
                refs[r["id"]] = {"status": "mismatch", "detail": "the topic at this position differs"}
            elif r["clock_domain"] in (None, LOG) and r["time_ns"] is not None and got[1] != r["time_ns"]:
                refs[r["id"]] = {"status": "mismatch", "detail": "the log time at this position differs"}
            else:
                note = "" if r["clock_domain"] in (None, LOG) or r["time_ns"] is None else "the time is not in the log-time domain and was not compared"
                refs[r["id"]] = {"status": "verified", "detail": note}
    every = list(refs.values()) + list(artifacts.values())
    summary = {k: sum(1 for x in every if x["status"] == k) for k in ("verified", "mismatch", "not_checked")}
    return {"path": clean_text(str(path)), "refs": refs, "artifacts": artifacts, "summary": summary}
