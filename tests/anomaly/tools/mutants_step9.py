from tests.anomaly.tools.mutate import Mutant

CT = ("tests/anomaly/test_cli_anomaly.py",)
RT = ("tests/anomaly/test_provenance_replay.py",)
EX = "physicore_collector/anomaly/exitcodes.py"
SV = "physicore_collector/anomaly/service.py"
CLI = "physicore_collector/cli.py"
RP = "physicore_collector/anomaly/report.py"

MUTANTS = [
    Mutant("exit: findings change default exit code (X-19a)", EX, "    if fail_on_findings and finding_count >= 1:", "    if finding_count >= 1:", CT),
    Mutant("exit: flag ignored (X-19b)", EX, "    if fail_on_findings and finding_count >= 1:", "    if False:", CT),
    Mutant("exit: invalid config returns 1 (X-19d)", EX, "EXIT_INVALID_CONFIG = 3", "EXIT_INVALID_CONFIG = 1", CT),
    Mutant("exit: invalid config returns 2 (X-19d)", EX, "EXIT_INVALID_CONFIG = 3", "EXIT_INVALID_CONFIG = 2", CT),
    Mutant("exit: not inspectable returns 1 (X-19f)", EX, "EXIT_NOT_INSPECTABLE = 2", "EXIT_NOT_INSPECTABLE = 1", CT),
    Mutant("exit: inventory code 1 ignored (X-19g)", EX, "    if inventory_code == 1:", "    if False:", CT),
    Mutant("exit: inventory code 2 ignored (X-19f)", EX, "    if inventory_code == 2:", "    if False:", CT),
    Mutant("exit: tampered not a reason when flag absent (X-19g/h)", EX, '        reasons.append("evidence_integrity_or_completeness")', "        pass", CT),
    Mutant("exit: reasons not listed together (X-19h)", EX, '        reasons.append("findings_with_fail_on_findings")', '        reasons[:] = ["findings_with_fail_on_findings"]', CT),
    Mutant("svc: invalid config still analyses evidence (X-19d)", SV, "    if loaded.config is None:\n", "    if loaded.config is None and False:\n", CT),
    Mutant("svc: not-inspectable ignored (X-19f)", SV, "    if inv_code == 2:", "    if inv_code == 99:", CT),
    Mutant("svc: inventory code not used (X-19g)", SV, "inventory_code=inv_code, finding_count=len(out.findings)", "inventory_code=0, finding_count=len(out.findings)", CT),
    Mutant("svc: disabled detector shown as blind spot (X-19f)", SV, 'status=("blind_spot" if config.enabled(d.detector_id) else "not_configured")', 'status="blind_spot"', CT),
    Mutant("svc: detector filter ignored (filter)", SV, "    return [d for d in ds if chosen is None or d.detector_id in chosen]", "    return ds", CT),
    Mutant("svc: evidence digests dropped (identity)", SV, "    return [SourceRef(artifact_ref=a.ref_id, sha256=a.sha256) for a in inv.artifacts if a.kind == \"mcap_recording\" and a.sha256]", "    return []", CT),
    Mutant("cli: report not echoed with json flag (X-22)", CLI, "typer.echo(render_json(report) if json_output else render_text(report), nl=False)", "typer.echo(render_text(report), nl=False)", CT),
    Mutant("cli: exit code not propagated (X-25)", CLI, "    if report.exit_code:\n        raise typer.Exit(code=report.exit_code)", "    pass", CT),
    Mutant("cli: unknown detector accepted (X-19i)", CLI, "    if unknown:\n        raise typer.BadParameter", "    if False:\n        raise typer.BadParameter", CT),
    Mutant("cli: fail flag not forwarded (X-19b)", CLI, "detector_ids=detector or None, fail_on_findings=fail_on_findings)", "detector_ids=detector or None, fail_on_findings=False)", CT),
    Mutant("report: reasons text mutated (X-25)", RP, '+ (f" ({\', \'.join(report.exit_reasons)})" if report.exit_reasons else "")', '+ ""', CT),
    Mutant("report: status line dropped (X-08)", RP, '        lines.append(f"{r.detector_id} v{r.detector_version}: {r.status}"', '        lines.append(f"{r.detector_id} v{r.detector_version}:"', CT),
    Mutant("prov: log observation index off by one (X-03)", "physicore_collector/anomaly/detectors/tier_a_log.py", "topic=occ.topic, message_index=occ.index, order=occ.order,", "topic=occ.topic, message_index=occ.index + 1, order=occ.order,", RT),
    Mutant("prov: non-finite observation index off by one (X-03)", "physicore_collector/anomaly/detectors/tier_b_non_finite.py", "topic=topic, message_index=occ.index, order=occ.order, log_time_ns=occ.log_time_ns,", "topic=topic, message_index=occ.index + 1, order=occ.order, log_time_ns=occ.log_time_ns,", RT),
    Mutant("prov: timing observation log_time shifted (X-03)", "physicore_collector/anomaly/detectors/timing.py", "message_index=o.index, order=o.order, log_time_ns=o.log_time_ns)", "message_index=o.index, order=o.order, log_time_ns=o.log_time_ns + 1)", RT),
    Mutant("prov: array-length value wrong (X-03)", "physicore_collector/anomaly/detectors/tier_b_array_length.py", "field_path=cpath, value=n, value_repr=str(n))", "field_path=cpath, value=n + 1, value_repr=str(n))", RT),
    Mutant("report: findings unsorted (X-01)", RP, "    return sorted(findings, key=key)", "    return list(findings)", CT + RT + ("tests/anomaly/test_report_skeleton.py",)),
]
