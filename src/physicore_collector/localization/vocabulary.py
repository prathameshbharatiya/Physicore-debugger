"""Closed vocabularies of M3d (DESIGN 3.3, 4.3, 4.5, 5 P6). Every label, status, code, template and phrase an M3d report may contain is
listed here; the report models reject anything else. Generated sentences are built from the fixed templates in this module only."""
from __future__ import annotations

import re

from ..anomaly.findings import FORBIDDEN_TERMS as _M3C_FORBIDDEN_TERMS

# ---- statements and labels
STATEMENT_KINDS = ("observed", "derived_order", "declared")
SYMPTOM_SOURCES = ("m3c_finding", "m2_finding")
ONSET_BASES = ("received_message", "silence_between_captured_messages", "window_span", "received_sample", "deadline_window", "payload_stamp_value")
RULE_IDS = ("interval_order.strict_precedence", "edge_consistency.declared_window")
ORDER_RELATIONS = ("before", "unordered")
LINK_LABELS = ("consistent_with_propagation", "inconsistent_order", "outside_declared_window", "undetermined")
PATH_KINDS = ("direct", "via_nodes")
DEFENCE_STATES = ("defended", "not_defended", "tied", "none_observed")
CHAIN_DEFENCE = ("defended_structure", "not_defended")
EDGE_RELATIONS = ("data_flow", "command_response", "dependency")
IDENTITY_KINDS = ("logger_name", "diagnostic_name", "hardware_id", "goal_id", "battery_serial", "battery_location")
NORMALISER_VERSION = "1.0.0"
NODE_COVERAGE = ("covered", "not_covered")
CHAINS_TOTAL_STATUS = ("exact", "bounded_unknown")

# ---- blockers and caveats (DESIGN 4.3 + clarification B)
BLOCKER_CODES = ("recording_incomplete", "detector_blind_spot", "detector_insufficient_evidence", "undecodable_messages", "unparseable_messages",
                 "findings_truncated", "observations_truncated", "m2_expectation_not_evaluated", "m2_blind_spot_before_or_unplaced",
                 "symptom_cap_reached", "link_cap_reached", "chains_truncated", "path_work_limit_reached")
CAVEAT_CODES = ("boundary_gaps_not_evaluated", "receipt_order_not_occurrence_order", "capture_loss_indistinguishable_from_silence",
                "baseline_from_recording", "baseline_contaminated_possible", "detector_not_configured", "detector_not_applicable",
                "real_recording_validation_outstanding")
BLOCKER_SOURCE_KINDS = ("inventory_record", "detector_result", "detector_counter", "m2_result", "m3d_limit", "finding")

# ---- uncertainty added by M3d (DESIGN P5)
M3D_UNCERTAINTY = ("onset_interval_width", "declared_edge_unverified", "path_via_unobserved_nodes", "blocked_defence")

# ---- hypotheses (DESIGN 4.5, D10: exactly three templates)
HYPOTHESIS_TEMPLATES = ("propagation_along_declared_edge", "common_or_independent_origin_not_excluded", "observation_or_timing_artifact_not_excluded")
HYPOTHESIS_STATUSES = ("consistent_with", "excluded_by_declared_edge_timing", "not_excluded")
DISCRIMINATING_EVIDENCE = (
    "an intervention or a repeat recording",
    "recorder-side drop counters (not available in this format)",
    "a declared edge for the ancestor",
    "detector coverage of the ancestor's topics",
    "a recording with the missing segments",
    "a narrower onset interval from a higher-rate topic",
)

# ---- fixed hypothesis sentences (DESIGN 4.5); templates 2 and 3 are always `not_excluded`
HYPOTHESIS_SENTENCES = {
    ("propagation_along_declared_edge", "consistent_with"): "The order of these two symptoms is consistent with the declared edge.",
    ("propagation_along_declared_edge", "excluded_by_declared_edge_timing"):
        "The order or spacing of these two symptoms does not fit the declared edge's direction or window. This concerns this declared edge only.",
    ("propagation_along_declared_edge", "not_excluded"): "The intervals overlap, so the declared edge is neither supported nor excluded for these two symptoms.",
    ("common_or_independent_origin_not_excluded", "not_excluded"):
        "A common or independent source of these symptoms is not excluded by the declared dependencies or the observations.",
    ("observation_or_timing_artifact_not_excluded", "not_excluded"):
        "An artifact of observation or timing is not excluded: recorder receipt time is not occurrence time, and the recording may be incomplete.",
}

# ---- defaults of the approved limits (D8 + clarification B); configurable only downward, recorded in every report
DEFAULT_LIMITS = {
    "symptoms": 100_000, "links": 200_000, "chains_reported": 1_000,
    "dependency_file_bytes": 1_048_576, "dependency_nodes": 5_000, "dependency_edges": 5_000, "dependency_string_chars": 256,
    "path_depth": 32, "paths_per_pair": 10_000, "path_work_limit": 1_000_000, "count_work_limit": 2_000_000,
}

# ---- fixed sentences
SCOPE_SENTENCE = ("This is the earliest symptom observed by the detectors that ran, under their configured parameters, "
                  "using recorder receipt time. It is not a statement about which event occurred first in the robot.")
NOT_DEFENDED_SENTENCE = ("The earliest observed symptom cannot be defended: at least one condition listed as a blocker could have hidden an earlier symptom, "
                         "or the symptom's own evidence is incomplete.")
TIED_SENTENCE = "Two or more symptoms cannot be ordered against each other at the start of this lane; none of them is the earliest."
NONE_OBSERVED_SENTENCE = ("No symptom was observed in this lane by the detectors that ran. This is a statement about those detectors, "
                          "not about the system.")
NOT_COMPARED_ACROSS_LANES = "Earliest symptoms of different clock domains are unordered across lanes; they are never compared."
SEVERITY_DISCLAIMER = ("Severity labels are copied from the source findings. They are not used for ordering, grouping or aggregation "
                       "and carry no statement of impact.")

# ---- wording control (P6). The M3c list applies, plus the M3d additions.
M3D_FORBIDDEN_TERMS = ("origin", "originated", "culprit", "responsible", "blame", "fault in", "explains", "therefore", "likelihood", "most likely")
FORBIDDEN_TERMS = tuple(dict.fromkeys(_M3C_FORBIDDEN_TERMS + M3D_FORBIDDEN_TERMS))
# The only tokens in which "propagat..." / "origin" may appear: the approved label and the three approved hypothesis template names (DESIGN 4.4, 4.5).
ALLOWED_TOKENS = ("consistent_with_propagation", "propagation_along_declared_edge", "common_or_independent_origin_not_excluded",
                  "observation_or_timing_artifact_not_excluded")
_FORBIDDEN_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(re.escape(t) for t in sorted(FORBIDDEN_TERMS, key=len, reverse=True)) + r")(?![A-Za-z])", re.IGNORECASE)
_PROPAGATION_RE = re.compile(r"propagat", re.IGNORECASE)

# keys/values that must never appear anywhere in a report (FR-RPT-02)
FORBIDDEN_KEY_WORDS = ("confidence", "probability", "score", "rank", "root_cause", "likelihood", "weight")
_KEY_RE = re.compile(r"(?<![a-z])(" + "|".join(FORBIDDEN_KEY_WORDS) + r")(?![a-z])", re.IGNORECASE)


def find_forbidden(text: str) -> list[str]:
    """Forbidden wording in a generated string. The label token `consistent_with_propagation` is exempt; "propagation" anywhere else is itself forbidden
    (the token is the only place the word may appear, DESIGN P6)."""
    stripped = text
    for tok in ALLOWED_TOKENS:
        stripped = stripped.replace(tok, " ")
    hits = {m.group(1).lower() for m in _FORBIDDEN_RE.finditer(stripped)}
    if _PROPAGATION_RE.search(stripped):
        hits.add("propagation")
    return sorted(hits)


def forbidden_key_words(name: str) -> list[str]:
    """Forbidden key words in a field name (underscore-separated words are matched as words)."""
    spaced = name.replace("_", " ")
    return sorted({m.group(1).lower() for m in _KEY_RE.finditer(spaced)} | ({"root_cause"} if "root_cause" in name.lower() else set()))
