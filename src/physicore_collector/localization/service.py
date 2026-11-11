"""Orchestration of one `evidence localize` run (Step 8; DESIGN 3.1, 3.4, 7).

Order: validate every input file first (any invalid -> exit 3, nothing analysed, even when the evidence is also bad); inspect the evidence and run the
accepted anomaly detection (and the accepted expectation investigation when a contract is given); bind the two runs to the same evidence by digest; build
symptoms, blockers, lanes; with declared dependencies add links, components, chains and hypotheses. Read-only; nothing is written anywhere. Never raises
for bad input or bad evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

from physicore_collector.inventory import exit_code as inventory_exit_code, inspect_evidence
from physicore_collector.versions import TOOL_VERSION

from . import blockers as B
from . import chains as H
from . import consistency as C
from .dependencies import WorkBudget, load_dependencies
from .output import Coverage, DependencyInput, DetectorStatus, EvidenceDigest, Inputs, LIMITATIONS, LocalizationReport, Problem
from .precedence import build_lanes
from .symptoms import apply_cap, from_m2, from_m3c, from_signal_profile
from .vocabulary import DEFAULT_LIMITS, SEVERITY_DISCLAIMER

LOG_LANE = "mcap_log_time"


def _hash(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _empty_chain_analysis() -> H.ChainAnalysis:
    return H.ChainAnalysis(components=[], chains=[], chains_reported=0, chains_total=0, chains_total_status="exact", chains_lower_bound=0,
                           chains_truncated_by=None, link_hypotheses=[], localization=H.Localization(components=[], unlocalized=[], topic_layers=[]))


def _reasons(codes: Iterable[str]) -> list[str]:
    order = ["anomaly_config_invalid", "expectations_invalid", "dependencies_invalid", "evidence_not_inspectable", "evidence_partial_or_unverified",
             "evidence_changed_during_run", "completed"]
    s = set(codes)
    return [r for r in order if r in s]


def _base(*, limits, params_hash, cfg_hash, exp_sha, dep, problems, code, reasons, detectors=None, evidence=None) -> LocalizationReport:
    return LocalizationReport(
        tool_version=TOOL_VERSION, evidence=evidence or [], inputs=Inputs(anomaly_config_hash=cfg_hash, expectations_sha256=exp_sha, dependencies=dep),
        parameters_hash=params_hash, limits=limits, lanes=[], symptoms=[], links=[], components=[], chains=[], chains_total=0, chains_total_status="exact",
        hypotheses=[], blockers=[], caveats=[], localization=H.Localization(components=[], unlocalized=[], topic_layers=[]),
        coverage=Coverage(dependencies="declared" if dep else "none_declared", detectors=detectors or []),
        limitations=list(LIMITATIONS), severity_disclaimer=SEVERITY_DISCLAIMER, exit_code=code, exit_reasons=reasons, problems=problems or None)


def run_localization(path: str | Path, anomaly_config: str | Path, *, expectations: str | Path | None = None, dependencies: str | Path | None = None,
                     detector_ids: Iterable[str] | None = None, limits: dict | None = None,
                     signal_profile: str | Path | None = None, registry: str | Path | None = None) -> LocalizationReport:
    from physicore_collector.anomaly.config import load_config
    from physicore_collector.anomaly.service import run_anomaly_detection

    lim = {**DEFAULT_LIMITS, **{k: min(int(v), DEFAULT_LIMITS[k]) for k, v in (limits or {}).items() if k in DEFAULT_LIMITS}}
    detector_ids = sorted(set(detector_ids)) if detector_ids else None
    if (signal_profile is None) != (registry is None):
        raise ValueError("signal_profile and registry must be supplied together")

    # 1. every input file is validated before anything is analysed
    problems: list[Problem] = []
    reasons: list[str] = []
    cfg = load_config(anomaly_config)
    if cfg.config is None:
        reasons.append("anomaly_config_invalid")
        problems += [Problem(source="anomaly_config", path=p.path, message=p.message) for p in cfg.problems]
    contract = None
    if expectations is not None:
        from physicore_collector.investigator.contract import load_contract
        contract = load_contract(expectations)
        if contract.contract is None:
            reasons.append("expectations_invalid")
            problems += [Problem(source="expectations", path=p.location or "", message=f"{p.code}: {p.message}") for p in contract.problems]
    deps = None
    if dependencies is not None:
        deps = load_dependencies(dependencies, limits=lim)
        if deps.graph is None:
            reasons.append("dependencies_invalid")
            problems += [Problem(source="dependencies", path=p.path, message=p.message) for p in deps.problems]
    cfg_hash = cfg.config.config_hash if cfg.config is not None else None
    exp_sha = contract.sha256 if contract is not None and contract.contract is not None else None
    dep_in = (DependencyInput(sha256=deps.sha256 or "", semantic_hash=deps.graph.semantic_hash, edge_count=len(deps.graph.edges))
              if deps is not None and deps.graph is not None else None)
    params_hash = _hash({"config": cfg_hash, "detectors": detector_ids, "limits": lim, "expectations": exp_sha, "dependencies": dep_in.semantic_hash if dep_in else None, "signal_profile": str(signal_profile) if signal_profile else None, "registry": str(registry) if registry else None})
    if reasons:
        return _base(limits=lim, params_hash=params_hash, cfg_hash=cfg_hash, exp_sha=exp_sha, dep=dep_in, problems=problems, code=3, reasons=_reasons(reasons))

    # 2. evidence: one inspection for the blockers, one anomaly run; they must describe the same recording
    inv = inspect_evidence(path)
    inv_code = inventory_exit_code(inv)
    report = run_anomaly_detection(path, anomaly_config, detector_ids=detector_ids)
    dets = [DetectorStatus(detector_id=r.detector_id, status=r.status) for r in report.results]
    ev = [EvidenceDigest(artifact_ref=e.artifact_ref, sha256=e.sha256) for e in report.evidence]
    if inv_code == 2:
        return _base(limits=lim, params_hash=params_hash, cfg_hash=cfg_hash, exp_sha=exp_sha, dep=dep_in, problems=None, code=2,
                     reasons=["evidence_not_inspectable"], detectors=dets, evidence=ev)
    inv_digests = sorted((a.ref_id, a.sha256) for a in inv.artifacts if a.kind == "mcap_recording" and a.sha256)
    changed = inv_digests != sorted((e.artifact_ref, e.sha256) for e in report.evidence)
    exit_codes = [] if not (inv_code == 1 or report.exit_code == 1) else ["evidence_partial_or_unverified"]
    if changed:
        exit_codes.append("evidence_changed_during_run")
        return _base(limits=lim, params_hash=params_hash, cfg_hash=cfg_hash, exp_sha=exp_sha, dep=dep_in, problems=None, code=1,
                     reasons=_reasons(exit_codes), detectors=dets, evidence=ev)

    m2 = None
    if contract is not None:
        from physicore_collector.investigator import investigate
        m2 = investigate(inv, contract)

    signal_profile_report = None
    if signal_profile is not None:
        from physicore_collector.anomaly.profile_pipeline import run_signal_profile_detection
        try:
            signal_profile_report = run_signal_profile_detection(path, signal_profile, registry)
        except (OSError, ValueError) as exc:
            return _base(limits=lim, params_hash=params_hash, cfg_hash=cfg_hash, exp_sha=exp_sha, dep=dep_in,
                         problems=[Problem(source="signal_profile", path=str(signal_profile), message=str(exc))],
                         code=3, reasons=["signal_profile_invalid"], detectors=dets, evidence=ev)

    # 3. symptoms, blockers, lanes
    norm = from_m3c(report)
    syms = list(norm.symptoms)
    norm_problems = list(norm.problems)
    if signal_profile_report is not None:
        nsp = from_signal_profile(signal_profile_report)
        syms += nsp.symptoms
        norm_problems += nsp.problems
    if m2 is not None:
        n2 = from_m2(m2)
        syms += n2.symptoms
        norm_problems += n2.problems
    syms, suppressed = apply_cap(syms, int(lim["symptoms"]))
    bset = B.build_blockers(report, inv, m2, syms, suppressed)
    blockers = list(bset.blockers)
    from physicore_collector.anomaly.findings import DETECTOR_IDS
    ran = {r.detector_id for r in report.results}
    caveats = list(bset.caveats) + [B.Caveat(code="detector_not_configured", scope=B.BlockerScope(detector=d), detail=B.CAVEAT_DETAIL["detector_not_configured"])
                                    for d in DETECTOR_IDS if d not in ran]          # a detector deselected with --detector says nothing, and says so
    lane_names = {LOG_LANE} | {ln for s in syms for ln in s.lanes}
    by_lane = B.blockers_by_lane(blockers, lane_names)

    consistency = None
    chain_analysis = _empty_chain_analysis()
    graph = deps.graph if deps is not None else None
    cov = None
    if graph is not None:
        cov = C.detector_coverage(report, graph)
        budget = WorkBudget(int(lim["path_work_limit"]))
        consistency, link_blockers = C.analyse_links(syms, graph, report, limits=lim, coverage=cov, budget=budget)
        blockers += link_blockers
        syms = C.apply_assignments(syms, consistency.assignments)
        by_lane = B.blockers_by_lane(blockers, lane_names)
        chain_analysis, chain_blockers = H.analyse_chains(syms, consistency.links, consistency, graph, blockers=blockers, caveats=caveats, coverage=cov,
                                                          budget=budget, limits=lim)
        blockers += chain_blockers
        by_lane = B.blockers_by_lane(blockers, lane_names)
    lanes, cross = build_lanes(syms, by_lane, extra_lanes=lane_names)

    if norm_problems:
        problems += [Problem(source="normalisation", path=p.source_id, message=f"{p.code}: {p.detail}") for p in norm_problems]
    coverage = Coverage(
        dependencies="declared" if graph is not None else "none_declared", detectors=dets,
        m2_status=(m2.status if m2 is not None else None),
        edge_coverage=(consistency.edge_coverage if consistency else []),
        links_total=(consistency.links_total if consistency else 0), links_suppressed=(consistency.links_suppressed if consistency else 0),
        links_status=(consistency.links_status if consistency else None),
        path_pairs_related=(consistency.path_pairs_related if consistency else 0), path_pairs_examined=(consistency.path_pairs_examined if consistency else 0),
        path_pairs_not_examined=(consistency.path_pairs_not_examined if consistency else 0),
        no_declared_relation_count=(consistency.no_declared_relation_count if consistency else 0),
        cross_lane_pairs_not_compared=(consistency.cross_lane_pairs_not_compared if consistency else 0), cross_lane_statement=cross,
        chains_reported=chain_analysis.chains_reported, chains_lower_bound=chain_analysis.chains_lower_bound, chains_truncated_by=chain_analysis.chains_truncated_by,
        symptoms_suppressed=suppressed)
    code = 1 if exit_codes else 0
    reasons = _reasons(exit_codes or ["completed"])
    return LocalizationReport(
        tool_version=TOOL_VERSION, evidence=ev, inputs=Inputs(anomaly_config_hash=cfg_hash, expectations_sha256=exp_sha, dependencies=dep_in),
        parameters_hash=params_hash, limits=lim, lanes=lanes, symptoms=sorted(syms, key=lambda s: s.symptom_id),
        links=(consistency.links if consistency else []), components=chain_analysis.components, chains=chain_analysis.chains,
        chains_total=chain_analysis.chains_total, chains_total_status=chain_analysis.chains_total_status, hypotheses=chain_analysis.link_hypotheses,
        blockers=blockers, caveats=caveats, localization=chain_analysis.localization, coverage=coverage, limitations=list(LIMITATIONS),
        severity_disclaimer=SEVERITY_DISCLAIMER, exit_code=code, exit_reasons=reasons, problems=problems or None)
