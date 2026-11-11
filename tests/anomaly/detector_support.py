"""Helpers to run a real detector over a synthetic JSON or CDR recording through the runner."""
from __future__ import annotations

from physicore_collector.anomaly.config import validate_config
from physicore_collector.anomaly.runner import run, topic_schema_names, wanted_topics
from physicore_collector.inventory import inspect_evidence
from physicore_collector.investigator.observations import load_evidence

LIMITS = {"max_retained_observations": 5, "max_text_bytes": 64, "max_findings_per_detector": 100}


def cfg(detectors: dict, limits: dict | None = None):
    lc = validate_config({"anomaly_config_version": 1, "limits": {**LIMITS, **(limits or {})}, "detectors": detectors})
    assert lc.config is not None, lc.problems
    return lc.config


def run_detectors(path, detectors, config):
    inv = inspect_evidence(path)
    wanted = wanted_topics(detectors, topic_schema_names(inv), config)
    ev = load_evidence(inv, wanted)
    return run(inv, ev, config, detectors), ev, inv


def result_of(out, detector_id):
    return next(r for r in out.results if r.detector_id == detector_id)
