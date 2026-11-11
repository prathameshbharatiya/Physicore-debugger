"""CS-04j: finding-id mutants m1-m3 (+ one at the detector layer). Each must fail at least one of CS-04b / 04c / 04i."""
from tests.anomaly.tools.mutate import Mutant

ID = ("tests/anomaly/test_finding_identity.py",)
FI = "physicore_collector/anomaly/findings.py"
BF = "physicore_collector/anomaly/detectors/base.py"
LINE = '"contributing_artifacts": [{"artifact_ref": r, "sha256": s} for r, s in arts],'

MUTANTS = [
    Mutant("m1: id ignores contributing artifacts (CS-04b/c/i)", FI, LINE, '"contributing_artifacts": [],', ID),
    Mutant("m2: id uses only the first artifact digest (CS-04c/i)", FI, LINE, '"contributing_artifacts": [{"artifact_ref": r, "sha256": s} for r, s in arts[:1]],', ID),
    Mutant("m3: id uses the artifact path instead of the digest (CS-04b/c/i)", FI, LINE, '"contributing_artifacts": [{"artifact_ref": r, "sha256": r} for r, s in arts],', ID),
    Mutant("m4: id from retained observation artifacts only (CS-04i)", BF, "        arts = sorted(set(artifacts)) if artifacts is not None else sorted(g.artifacts)",
           "        arts = sorted(set(artifacts)) if artifacts is not None else sorted({(o.artifact_ref, o.sha256) for o in g.obs})", ID),
    Mutant("m5: id artifacts unsorted-set order dependent (CS-04f)", FI, "    arts = sorted(set((str(r), str(s)) for r, s in contributing_artifacts))", "    arts = list(dict.fromkeys((str(r), str(s)) for r, s in contributing_artifacts))", ID + ("tests/anomaly/test_findings_schema.py",)),
    Mutant("m6: id ignores sha but keeps ref (CS-04b)", FI, LINE, '"contributing_artifacts": [{"artifact_ref": r, "sha256": ""} for r, s in arts],', ID),
]
