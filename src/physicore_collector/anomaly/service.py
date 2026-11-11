"""Orchestration of one `evidence detect-anomalies` run (DESIGN 5.4/5.5): validate the configuration first (invalid => nothing is analysed),
inspect the evidence with the accepted inventory, load only the topics the enabled detectors may need, run the detectors, assemble the report and
decide the exit code with the accepted `inventory.exit_code`. Read-only: nothing is written anywhere. Never raises for a bad config or bad evidence."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from physicore_collector.inventory import exit_code as inventory_exit_code, inspect_evidence
from physicore_collector.investigator.observations import load_evidence
from physicore_collector.versions import TOOL_VERSION

from .config import load_config
from .detectors.tier_a_battery import BatteryDetector
from .detectors.tier_a_diagnostics import DiagnosticsDetector
from .detectors.tier_a_goal_status import GoalStatusDetector
from .detectors.tier_a_log import RosLogDetector
from .detectors.tier_b_array_length import ArrayLengthDetector
from .detectors.tier_b_gap import PublishingGapDetector
from .detectors.tier_b_non_finite import NonFiniteDetector
from .detectors.tier_b_rate import RateChangeDetector
from .detectors.tier_b_stamp_order import StampOrderDetector
from .exitcodes import decide_exit
from .findings import AnomalyReport, DETECTOR_IDS, DetectorResult, Reason, SourceRef
from .report import new_report
from .runner import run, topic_schema_names, wanted_topics

DETECTOR_CLASSES = (RosLogDetector, DiagnosticsDetector, GoalStatusDetector, BatteryDetector, PublishingGapDetector, RateChangeDetector,
                    StampOrderDetector, ArrayLengthDetector, NonFiniteDetector)


def all_detectors(only: Iterable[str] | None = None):
    chosen = set(only) if only else None
    ds = [c() for c in DETECTOR_CLASSES]
    assert {d.detector_id for d in ds} == set(DETECTOR_IDS)
    return [d for d in ds if chosen is None or d.detector_id in chosen]


def _evidence_refs(inv) -> list[SourceRef]:
    return [SourceRef(artifact_ref=a.ref_id, sha256=a.sha256) for a in inv.artifacts if a.kind == "mcap_recording" and a.sha256]


def run_anomaly_detection(path: str | Path, config_path: str | Path, *, detector_ids: Iterable[str] | None = None,
                          fail_on_findings: bool = False) -> AnomalyReport:
    """-> the complete report, with `exit_code` / `exit_reasons` already decided."""
    loaded = load_config(config_path)
    if loaded.config is None:
        code, reasons = decide_exit(config_valid=False, inventory_code=None, finding_count=0, fail_on_findings=fail_on_findings)
        return new_report(tool_version=TOOL_VERSION, exit_code=code, exit_reasons=reasons,
                          config_problems=[{"path": p.path, "message": p.message} for p in loaded.problems])
    config = loaded.config
    detectors = all_detectors(detector_ids)
    inv = inspect_evidence(path)
    inv_code = inventory_exit_code(inv)
    if inv_code == 2:
        results = [DetectorResult(detector_id=d.detector_id, detector_version=d.detector_version,
                                  status=("blind_spot" if config.enabled(d.detector_id) else "not_configured"),
                                  reasons=([Reason(code="evidence_not_inspectable")] if config.enabled(d.detector_id) else []))
                   for d in detectors]
        code, reasons = decide_exit(config_valid=True, inventory_code=2, finding_count=0, fail_on_findings=fail_on_findings)
        return new_report(tool_version=TOOL_VERSION, exit_code=code, exit_reasons=reasons, config_hash=config.config_hash, results=results)
    ev = load_evidence(inv, wanted_topics(detectors, topic_schema_names(inv), config))
    out = run(inv, ev, config, detectors, track_decodes=False)
    code, reasons = decide_exit(config_valid=True, inventory_code=inv_code, finding_count=len(out.findings), fail_on_findings=fail_on_findings)
    return new_report(tool_version=TOOL_VERSION, exit_code=code, exit_reasons=reasons, config_hash=config.config_hash,
                      evidence=_evidence_refs(inv), results=out.results, findings=out.findings)
