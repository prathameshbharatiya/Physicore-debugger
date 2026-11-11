# STEP 8 REPORT — service, full report, readable renderer, `evidence localize`

Branch `m3d-baseline` (local only; no push, no remote). M3c base `b7bf26044b49…`. Step 7 HEAD before this step: `f4ede3a`.

## Files changed
New (all under the allowed M3d paths):
- `src/physicore_collector/localization/output.py` — `LocalizationReport` (strict, `extra=forbid`), `Coverage`, `Inputs`, `Problem`, canonical `to_json`, fixed-template `render_text`, `render_lines` (template proof), `inert()`.
- `src/physicore_collector/localization/service.py` — `run_localization(...)`.
- `tests/localization/test_svc.py` (58 tests), `tests/localization/tools/mutants_step8.py` (61 mutants).

Edited:
- `src/physicore_collector/cli.py` — ONE additive `evidence localize` command appended at the end of the file: 31 lines added, 0 removed or modified (`git diff` verified; PRES-04 passes). Lazy imports; M3c's `investigator`/`cli` still do not import the anomaly package at import time (accepted PRES_09 import test passes).
- `tests/localization/test_preservation.py` — PRES-05 (M3d's own test): the `evidence` group help is now required to equal the accepted golden **plus exactly the `localize` row** (every other byte identical). All other accepted help goldens remain byte-identical. No golden file was changed.

Not touched: any M3c file, any accepted test, any accepted baseline or pinned deliverable, `pyproject.toml`.

## Behaviour implemented
- Inputs validated first (config, expectations, dependencies); any invalid → exit 3 with `problems`, nothing analysed, even if the evidence is also unreadable. `exit_reasons` follow the design precedence (anomaly_config_invalid, expectations_invalid, dependencies_invalid, evidence_not_inspectable, evidence_partial_or_unverified, evidence_changed_during_run, completed).
- Evidence: one `inspect_evidence` plus one accepted M3c run, bound by comparing artifact digests; mismatch → exit 1, `evidence_changed_during_run`, no analysis. Inventory code 2 → exit 2. Partial (either signal) → exit 1 with the full report.
- M2 divergences included when `--expectations` is given. Symptom cap with exact suppressed count. Blockers, lanes (log lane always present), links, components, chains, link hypotheses as in Steps 5–7; without `--dependencies` the report is order-only and says "No dependencies declared".
- Detectors deselected with `--detector` are reported as `detector_not_configured` caveats (they were otherwise absent from M3c's results).
- Limits can only be lowered (`min` with the approved defaults), recorded in the report; `parameters_hash` covers config hash, detectors, limits, expectations sha, dependency semantic hash.
- No `--fail-on-*`; `--dependencies -` (and `-` for the other files) is a usage error; unknown detector id → usage error listing valid ids.

## Tests
- `tests/localization/test_svc.py`: **58 passed** in each environment. Rows covered: CLI-01..17, 19, 20 (CLI-18 via the precedence tests), RPT-01..06, WRD-01..06, PRV-02, 03, 05, 06, 07, 08, 09 (weak form), 10, 12, ADV-19, 21 (weak), 22, 23, 24, 25, plus limits, digest-binding, exit-signal and renderer-detail tests.
- Full suite: ros2 venv **2299 passed, 6 skipped, 1 failed**; plain venv **2197 passed, 108 skipped, 1 failed** (was 2241/2139 passed after Step 7; +58 each). **The full suite is NOT clean: the single failure in each environment is PRES_09, below.**

## Mutation (MUT-CLI, MUT-WRD, MUT-PRV families, step 8 file)
61 mutants: **61 killed, 0 survived, 0 equivalent** (control passes). First run: 42 killed / 20 survived (log kept as `STEP8_MUTATION_RUN1.log`); survivors were closed with targeted tests (reason ordering, digest binding, exit signals, real M2 contract, limits cannot be raised, renderer details), one vacuous mutant (a no-op) was removed, and two last survivors were killed by strengthened assertions; the final 59+2 mutants were re-run and killed.
One real defect was found by a surviving mutant and fixed: two renderer templates were bare `{sentence}` placeholders and matched any line, which made the "every sentence matches a fixed template" proof (WRD-04) vacuous for those lines. They now carry fixed prefixes ("Statement for this lane:", "Across lanes:"), and a test asserts that no template is a bare placeholder.

## PRES_09 (reported separately; known isolated failure; NOT fixed, NOT exempted)
`tests/anomaly/test_preservation.py::test_PRES_09_no_accepted_module_mentions_the_anomaly_package_before_step_9` fails in both environments. It is a purely lexical check (`"anomaly" in file text`) and is the only failure. Files outside `anomaly/` and `cli.py` that contain the word: `localization/{consistency,intervals,symptoms,vocabulary,blockers,output,service}.py` (seven; two new in this step). No accepted (non-localization) module offends; `git diff --name-only b7bf260 -- src tests` outside `localization/` shows only `cli.py` and `tests/localization/test_preservation.py`. The companion import test `test_PRES_09_investigator_does_not_import_anomaly` passes. The accepted test and M3c source are unmodified; no `/localization/` exemption was added. This needs a separate explicit decision.

## Deviations / interpretations (each recorded here, none changes an approved contract)
1. The report model lives in new `output.py`, not in a "completed `report.py`" (avoids an import cycle with the statement types in `report.py`).
2. `hypotheses` carries the per-link hypotheses; per-chain hypotheses live inside each chain; chain lower bound, truncation reason, edge coverage, counters and the cross-lane statement live under `coverage`.
3. `problems` is required when exit code is 3 and may also carry normalisation problems on exit 0/1 (exit code unchanged), as planned.
4. PRES-05 adapted for the `evidence` group help as described above (additive row only).
5. `--detector` deselection produces `detector_not_configured` caveats in M3d (M3c omits deselected detectors from its results).

## Limitations / not yet done (stated, not claimed)
- PRV-01, 04, 11 (delivered M3c example ZIP), 13..16 and the independent replay verifier are Step 9; PRV-09 is covered here only in weak form (a tampered artifact is not silently accepted). ADV-20 and ADV-21 are covered only weakly; SCL, DET and bench tests are Step 9. CLI-20 runs the command in a subprocess on a synthetic recording, not on the M3c example ZIP.
- Real-recording validation and decoder licensing clearance remain outstanding. Step 11 delivery still requires separate authorization.
- Earlier recorded refinements still open for the user: rate onset uses the first flagged window; the stamp-order retention rule; M2 transition onset is the deadline window.

## Commit
Recorded in git as the Step 8 commit (trailers present); SHA is written in the follow-up line below.

Step 8 commit: `c8ea8e2`
