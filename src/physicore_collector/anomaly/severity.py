"""Severity policy (DESIGN 4.3). Severity is an ordered label of an indicator class. It is never a calibrated failure
probability and never a statement of physical impact. `severity_basis` says where the label's input comes from."""
from __future__ import annotations

SEVERITIES = ("info", "notice", "warning", "error", "critical")
SEVERITY_BASES = ("source_declared_level", "detector_policy", "config")

# indicator id -> (severity, severity_basis)
SEVERITY_POLICY: dict[str, tuple[str, str]] = {
    "log.WARN": ("warning", "source_declared_level"),
    "log.ERROR": ("error", "source_declared_level"),
    "log.FATAL": ("critical", "source_declared_level"),
    "diagnostic.WARN": ("warning", "source_declared_level"),
    "diagnostic.ERROR": ("error", "source_declared_level"),
    "diagnostic.STALE": ("warning", "source_declared_level"),
    "goal.ABORTED": ("error", "detector_policy"),
    "goal.CANCELED": ("notice", "detector_policy"),
    "battery.health": ("warning", "detector_policy"),   # all seven health flags
    "battery.threshold": ("warning", "config"),
    "tierB": ("notice", "detector_policy"),            # every Tier B indicator
}


def severity_for(indicator: str) -> tuple[str, str]:
    """-> (severity, severity_basis). Unknown indicators raise: no silent default."""
    if indicator.startswith("tierB."):
        indicator = "tierB"
    try:
        return SEVERITY_POLICY[indicator]
    except KeyError:
        raise KeyError(f"no severity policy for indicator '{indicator}'") from None
