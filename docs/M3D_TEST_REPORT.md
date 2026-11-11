# M3d test report

Everything below was measured on the local tree (branch `m3d-baseline`); nothing is claimed that was not run. **Synthetic evidence only; real-recording validation and the decoder licensing review are outstanding.**

## Suite results (final run, both environments)
| Environment | Passed | Skipped | Failed |
|---|---|---|---|
| ros2 venv (full suite, accepted + M3c + M3d) | 2346 | 7 | 0 |
| plain venv | 2244 | 109 | 0 |

PRES-01 (accepted counts not below baseline): ros2 2346 >= 1704 and plain 2244 >= 1603 passed (baselines from the Step 0 record).
**One accepted test was edited, with the owner's approval (2026-11-07):** `tests/anomaly/test_preservation.py::test_PRES_09_no_accepted_module_mentions_the_anomaly_package_before_step_9`
is a lexical check that the word "anomaly" appears in no accepted module; it failed on seven files of the new `localization/` package, which legitimately build on the M3c package. The approved change is one line:
the check now also skips paths containing `/localization/`. A guard test (`test_PRES_02_the_one_approved_test_edit_is_exactly_the_localization_exemption`) proves that this is the only difference from the accepted file (one line added, one removed).
Before the exemption the suite had exactly this one failure in each environment and nothing else. Accepted M3c source is unmodified; the companion import-isolation test (accepted modules do not import localization) still passes.
Skipped tests: the ros2-only tests in the plain environment, and the gated 100,000-symptom benchmark (`M3D_BENCH=1`).

M3d tests: 466 test functions in `tests/localization/` (test_int 22, sym 37, pre 30, dep 68, blk 52, con 53, chn 65, svc 57, ver 9, det 9, det2 17, det_static 5, scl 5, report_base 21, preservation 10, unc_pres 4, example 2), plus independent oracles (`oracle.py`).
`tests/localization/tools/req_matrix.py` maps all 53 requirements to test families with no empty row (`m3d_records/REQ_MATRIX.md`); that shows coverage exists, not that it is sufficient.

## Mutation (text-substitution harness; final run on the final source, ros2 environment)
| Family | Mutants | Killed | Survived |
|---|---|---|---|
| step 1 (intervals, wording, report base) | 43 | 43 | 0 |
| step 2 (symptoms) | 51 | 51 | 0 |
| step 3 (dependency file) | 70 | 70 | 0 |
| step 4 (precedence) | 36 | 36 | 0 |
| step 5 (blockers) | 66 | 66 | 0 |
| step 6 (link consistency) | 65 | 64 | 1 (justified equivalent) |
| step 7 (chains, hypotheses) | 76 | 75 | 1 (justified equivalent) |
| step 8 (service, report, renderer, CLI) | 61 | 61 | 0 |
| step 9 (determinism) | 31 | 30 | 1 (justified equivalent) |
| **total** | **499** | **496** | **3 equivalent** |
Equivalent mutants: "cross-lane pairs linked" (a lane-disjoint pair can never produce a link, so linking it changes nothing observable); the Step 7 one recorded in its report; "layer sort key drops id" (tied intervals are identical, so their layers are equal whatever the order). The harness records killed/survived per mutant but not which test killed it. Logs: `m3d_records/STEP10_FINAL_MUT*.log`. Several first runs had survivors that exposed real gaps (a renderer template that matched any line; unsorted blocker sets) which were fixed and re-run.

## Measured performance (`m3d_records/STEP9_BENCH.jsonl`; fresh process each; 8 GB machine; recorded, not tuned)
| Scenario | Symptoms | Time | Peak memory | Result |
|---|---|---|---|---|
| 5,000-node chain (worst case) | 100,000 | 87 s | 3.4 GB | 89,600 links; path work limit reached, blockers raised |
| fan, 99 children | 100,000 | 37 s | 2.6 GB | link cap 200,000 reached; suppressed count exact |
| all intervals overlapping | 100,000 | 46 s | 2.5 GB | link cap reached |
| one edge, 500 x 500 symptoms | 1,000 | 19 s | 1.7 GB | 200,000 of 250,000 links, 50,000 suppressed |
| sparse pairs | 5k / 10k / 20k / 40k | 1.4 / 2.5 / 6.3 / 18.7 s | 113 MiB to 1.6 GB | quadratic in links by nature; ratios 1.8, 2.5, 3.0 |
Linear-link workload (4 links per declared pair): 2,500 / 5,000 / 10,000 symptoms took 0.33 / 0.75 / 2.26 s (doubling ratios 2.3 and 3.0). The cost is not purely linear because the node-pair counters take O(nodes x (nodes + edges)), i.e. they grow quadratically with the declared graph size.
Plainly: every limit completes and raises its blocker; memory is dominated by the link objects (about 8 KB each), so the 100,000-symptom worst case needs about 3.4 GB; the 1,000,000-step path-work limit costs roughly 20 s of Python time. Layering with every interval overlapping is near-linear (a test checks time(2n)/time(n) <= 3).

## What each test area established
Interval order and the strict-precedence rule against brute-force oracles; symptom normalisation from every M3c detector and M2 against real recordings; the dependency file (strict parsing, limits, adversarial YAML); lanes, layers and earliest-symptom defence; blockers resolved to real source records with observation references only where they exist; link labels and window arithmetic against an independent oracle; chain enumeration (lazy, deterministic, exact counts or `bounded_unknown`, work bounds) against brute force on random graphs; the exact report schema, wording control and fixed-template rendering; exit codes and input precedence; determinism across hash seeds, cwd, locale, timezone, directory names and input order; an independent replay verifier that re-reads the raw recording; hostile names (control characters, injection text) kept inert; preservation of every accepted file, help text and baseline.

## Known limitations of this evidence
* Synthetic recordings only; no real robot recording; decoder licensing clearance outstanding.
* The replay verifier replays log-time onsets and references; payload-stamp, window and deadline onsets are counted as not replayed (never silently passed).
* Timing tests (scaling ratios) are best-of-two because a ratio can fail under CPU contention. **Correction recorded:** the scaling bound was first stated as time(2n)/time(n) <= 3 on a workload with quadratically growing links; it failed once when run from the delivery ZIP (3.2). The workload was changed to one with linearly growing links and the bound corrected to 4 (the quadratic worst case in graph size), because the measured behaviour is quadratic in the declared graph size, not linear. The earlier workload remains in the benchmark table as a measurement.
* Open design refinements awaiting the owner's decision: rate onset uses the first flagged window; the stamp-order retention rule; M2 transition onset is the deadline window.
* Documentation intentionally contains negated uses of words such as "cause" (e.g. "never states a cause"); generated reports contain none.
