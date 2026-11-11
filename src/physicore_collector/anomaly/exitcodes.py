"""Exit-code decision of `evidence detect-anomalies` (DESIGN 5.5). Pure function: no I/O.

0 success (including when findings exist) | 1 findings with --fail-on-findings, or evidence integrity/completeness
(the accepted `inventory.exit_code`) | 2 evidence could not be inspected (usage errors are Click's own 2) | 3 invalid anomaly configuration."""
from __future__ import annotations

EXIT_OK = 0
EXIT_FINDINGS_OR_EVIDENCE = 1
EXIT_NOT_INSPECTABLE = 2
EXIT_INVALID_CONFIG = 3


def decide_exit(*, config_valid: bool, inventory_code: int | None, finding_count: int, fail_on_findings: bool) -> tuple[int, list[str]]:
    """-> (exit_code, exit_reasons). `inventory_code` is the accepted inventory exit code (0/1/2), or None when it was not computed."""
    if not config_valid:
        return EXIT_INVALID_CONFIG, ["invalid_config"]
    if inventory_code == 2:
        return EXIT_NOT_INSPECTABLE, ["evidence_not_inspectable"]
    reasons: list[str] = []
    if inventory_code == 1:
        reasons.append("evidence_integrity_or_completeness")
    if fail_on_findings and finding_count >= 1:
        reasons.append("findings_with_fail_on_findings")
    return (EXIT_FINDINGS_OR_EVIDENCE if reasons else EXIT_OK), reasons
