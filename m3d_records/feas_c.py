import sys, json
from pathlib import Path
from physicore_collector.anomaly.service import run_anomaly_detection
from physicore_collector.inventory import inspect_evidence, exit_code
from physicore_collector.investigator import investigate, load_contract
rec, cfg, exp = sys.argv[1:4]
rep = run_anomaly_detection(rec, cfg)
inv = inspect_evidence(rec)
print("M3c findings:", len(rep.findings), "exit", rep.exit_code, "evidence digests:", [(e.artifact_ref, e.sha256[:12]) for e in rep.evidence])
print("inventory artifacts:", [(a.ref_id, a.kind, (a.sha256 or '')[:12]) for a in inv.artifacts], "inv exit", exit_code(inv))
print("digest binding equal:", [(e.artifact_ref, e.sha256) for e in rep.evidence] == [(a.ref_id, a.sha256) for a in inv.artifacts if a.kind == "mcap_recording" and a.sha256])
print("inventory.integrity:", inv.integrity.model_dump() if hasattr(inv.integrity, "model_dump") else inv.integrity)
print("inventory.inspection:", inv.inspection.model_dump())
for f in rep.findings: print(" ", f.finding_id, f.anomaly_category, f.topic, f.first_observed_at.value_ns, f.last_observed_at and f.last_observed_at.value_ns, f.occurrence_count, f.observations_truncated)
r = investigate(inv, load_contract(exp))
print("M2 in-process on same inventory:", r.status.value, "candidates:", len(r.candidates), "results:", [(x.expectation_id, x.status.value, x.evidence_complete) for x in r.expectation_results])
for c in r.candidates: print("  M2 finding", c.finding_id, c.expectation_kind, c.time.ns, c.time.kind, c.time.clock_domain, [(s.role, s.observation_id) for s in c.sources])
