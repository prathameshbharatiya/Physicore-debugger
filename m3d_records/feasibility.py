"""Step 0 feasibility checks (a)-(d). Read-only; run with PYTHONPATH=work/src."""
import ast, inspect, importlib, pkgutil, sys, time, yaml
sys.setrecursionlimit(10000)
print("== (a) public accessors M3d needs")
import physicore_collector.anomaly.service as asvc, physicore_collector.anomaly.findings as af, physicore_collector.anomaly.clock as ac
import physicore_collector.investigator as inv, physicore_collector.inventory as invt
need = {
 "anomaly.service.run_anomaly_detection": asvc.run_anomaly_detection,
 "anomaly.service.all_detectors": asvc.all_detectors,
 "anomaly.findings.AnomalyReport": af.AnomalyReport, "anomaly.findings.Finding": af.Finding,
 "anomaly.findings.DetectorResult": af.DetectorResult, "anomaly.findings.ObservationRef": af.ObservationRef,
 "anomaly.findings.CATEGORIES": af.CATEGORIES, "anomaly.findings.find_forbidden": af.find_forbidden,
 "anomaly.findings.canonical_json": af.canonical_json,
 "anomaly.clock.ClockedValue": ac.ClockedValue, "anomaly.clock.ClockDomainError": ac.ClockDomainError, "anomaly.clock.LOG_TIME_DOMAIN": ac.LOG_TIME_DOMAIN,
 "investigator.investigate": inv.investigate, "investigator.load_contract": inv.load_contract, "investigator.InvestigationResult": inv.InvestigationResult,
 "inventory.inspect_evidence": invt.inspect_evidence, "inventory.exit_code": invt.exit_code,
}
for k,v in need.items(): print(f"  OK  {k}")
print("  signature run_anomaly_detection:", inspect.signature(asvc.run_anomaly_detection))
print("  signature investigate:", inspect.signature(inv.investigate))
print("  signature inspect_evidence:", inspect.signature(invt.inspect_evidence))
import physicore_collector.investigator.engine as eng
print("  M2 models public (no underscore):", [n for n in ("Finding","ExpectationResult","EvidenceSummary","TimePoint","SourceRef","BlindSpot") if hasattr(eng,n)])
print("  inventory fields:", [f for f in ("artifacts","integrity_state","inspection_state","blocked") if hasattr(invt.EvidenceInventory, "model_fields") and f in invt.EvidenceInventory.model_fields] if hasattr(invt,"EvidenceInventory") else "n/a")
print("== (b) category -> detector emitted category/time fields (static from source)")
import re,pathlib
base=pathlib.Path(asvc.__file__).parent/"detectors"
for p in sorted(base.glob("tier_*.py")):
    s=p.read_text()
    cats=sorted(set(re.findall(r'category="([a-z_]+)"',s))|set(re.findall(r'cat = "([a-z_]+)"',s)))
    times="times=" in s
    print(f"  {p.name:28s} categories={cats} explicit_times_arg={times}")
print("  CATEGORIES =",af.CATEGORIES)
print("== (d) yaml.safe_load behaviour")
bomb="a: &a [x,x,x,x,x,x,x,x,x]\nb: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]\nc: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]\nd: &d [*c,*c,*c,*c,*c,*c,*c,*c,*c]\ne: &e [*d,*d,*d,*d,*d,*d,*d,*d,*d]\n"
t=time.time(); r=yaml.safe_load(bomb); print("  anchors/aliases accepted by safe_load (shared refs, not expanded):", type(r["e"]).__name__, f"{time.time()-t:.4f}s  -> loader must refuse aliases itself (token scan)")
try: yaml.safe_load("x: !!python/object/apply:os.system ['echo hi']"); print("  python tag: accepted?!")
except Exception as e: print("  python tag refused by safe_load:", type(e).__name__)
for depth in (100,1000,10000):
    s="["*depth+"]"*depth
    try: t=time.time(); yaml.safe_load(s); print(f"  nesting {depth}: parsed {time.time()-t:.3f}s")
    except RecursionError: print(f"  nesting {depth}: RecursionError (must be caught/refused by loader)")
    except Exception as e: print(f"  nesting {depth}: {type(e).__name__}")
import json
for depth in (1000,100000):
    s="["*depth+"]"*depth
    try: json.loads(s); print(f"  json nesting {depth}: parsed")
    except RecursionError: print(f"  json nesting {depth}: RecursionError (must be caught/refused)")
print("  merge key '<<' accepted by safe_load:", yaml.safe_load("a: &x {k: 1}\nb: {<<: *x}") )
print("  duplicate keys accepted silently by safe_load:", yaml.safe_load("a: 1\na: 2"))
print("  yaml bool-ish scalars: 'on' ->", yaml.safe_load("x: on"), "; window 5e8 ->", repr(yaml.safe_load("x: 5e8")["x"]), "; 500_000_000 ->", repr(yaml.safe_load("x: 500_000_000")["x"]))
