# STEP 9 REPORT — replay verifier, determinism, scale, requirement matrix

Branch `m3d-baseline` (local only; no push). Step 8 HEAD before this step: `2c630ed`. M3c untouched.

## Files
New: `tests/localization/tools/{replay_verifier,bench,req_matrix,mutants_step9}.py`; `tests/localization/{test_ver,test_det,test_det2,test_scl}.py`; `m3d_records/{REQ_MATRIX.md,STEP9_BENCH.jsonl,STEP9_*.log}`.
Edited (M3d only): `consistency.py`, `dependencies.py` (performance, below), `tests/localization/tools/{mutants_step1,mutants_step6}.py` (stale patterns repaired).

## Source changes in this step (behaviour-neutral, found by the benchmark)
1. `DependencyGraph.reaches` is memoised (at most 256 endpoints, bounded memory); it was recomputed for every node pair (200 symptoms on a 5,000-node chain: 4.6 s -> 1.0 s).
2. `analyse_links` no longer keeps a set of every related node pair (3.4 GB at 10,000 symptoms on a 5,000-node chain); counters are accumulated while the pairs are visited, with a once-per-unordered-pair rule. Same outputs (all 275 Step 3/6/7 tests and the oracle comparisons pass unchanged). The unused `_pairs_without_lane` and an unused in-edge sort were removed.

## Tests added (all pass in both environments)
- `test_ver.py` (9): replay verifier on real recordings — every evidence reference, log-time onset (received_message, silence bounds), blocker artifact digests and observation references; tampered file, wrong index/topic/time/onset/digest and a fabricated observation are each flagged; observation-backed and metadata-only blockers; round-trip; the delivered M3c example package (PRV-11). The verifier imports nothing from M3d.
- `test_det.py` (9) + `test_det2.py` (17): hash seed, cwd, locale, timezone, tmp-dir name, finding order, edge/node order, repeated runs; hostile names (ADV-10/11b), M2-only symptoms with M3c scope caveats (ADV-20), two segments with a time reset (ADV-21); every ordering rule.
- `test_scl.py` (5, one heavy gated by `M3D_BENCH=1`): scaling ratio (SCL-05), layering with all intervals overlapping (SCL-07), link cap with exact suppressed count (SCL-02), refused 1000x1000 bipartite dependency file and 5,001 edges (SCL-08).
- `req_matrix.py`: 53 requirements, 0 empty rows (`REQ_MATRIX.md`). It shows that a test family exists for each requirement, not that it is sufficient.

## Mutation
- New determinism family: 31 mutants, 30 killed, 1 justified equivalent ("layer sort key drops id": tied intervals are identical).
- All earlier families re-run after the refactor (ros2 environment): step1 43/43, step2 51/51, step3 70/70, step4 36/36, step5 66/66, step6 64 + 1 known equivalent, step7 75 + 1 known equivalent, step8 61/61. Steps 1 and 6 first showed stale (invalid) patterns, which were repaired and re-run. Correction: the Step 7 report said 77 mutants; the file contains 76 (75 killed + 1 equivalent).

## Measured performance (`STEP9_BENCH.jsonl`; fresh process each, plain venv, 8 GB machine; recorded, not tuned)
| scenario | symptoms | time | peak RSS | notes |
|---|---|---|---|---|
| layers (5,000-node chain, worst case) | 10,000 | 38 s | 237 MiB | path work limit reached, blockers raised |
| layers | 100,000 | 87 s | 3.4 GB | 89,600 links; path work limit reached |
| fan (99 children) | 100,000 | 37 s | 2.6 GB | link cap 200,000, 98.8 M suppressed (exact) |
| overlap (all intervals overlap) | 100,000 | 46 s | 2.5 GB | link cap reached |
| bipartite | 1,000 | 19 s | 1.7 GB | 200,000 of 250,000 links, 50,000 suppressed |
| sparse | 5k / 10k / 20k / 40k | 1.4 / 2.5 / 6.3 / 18.7 s | 113 MiB .. 1.6 GB | links grow quadratically by nature; ratios 1.8, 2.5, 3.0 |
Findings stated plainly: (a) everything completes and every limit raises its blocker; (b) memory is dominated by the `Link` objects (about 8 KB each; 200,000 links is about 1.7 GB) and the 100,000-symptom worst case needs about 3.4 GB — this exceeds a comfortable budget on small machines and is NOT reduced here; (c) the work limit of 1,000,000 path steps costs roughly 20 s of Python time; (d) the SCL-05 test uses best-of-two because one run failed under CPU contention (two suites in parallel) and passed alone.

## Suite results
ros2 venv: **2338 passed, 7 skipped, 1 failed**. plain venv: see the line appended below. **The suite is not clean: the one failure is PRES_09** (lexical false positive; seven localization files mention the M3c package; the accepted test and M3c source are untouched; no exemption added; needs your explicit decision). The heavy SCL-01/06 100,000-symptom test is gated (`M3D_BENCH=1`) and not part of the suite; its numbers are the benchmark table above.

## Not done / limitations
- No real-recording validation and no decoder licensing clearance (outstanding). Step 11 delivery needs separate authorization.
- PRV-09 tamper handling and ADV-21 remain modest; replay verifies log-time bases only (payload-stamp, window and deadline onsets are counted as `not_replayed`, never passed silently).
- Open refinements: rate onset uses the first flagged window; stamp-order retention rule; M2 transition onset is the deadline window.

plain venv: **2236 passed, 109 skipped, 1 failed (PRES_09)**.

Step 9 commit: `538e944`
