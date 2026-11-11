# M3d — Implementation Plan (Revision 1 — APPROVED; Step 0 authorized 2026-11-03)

Status: **Approved with clarifications A and B (see `M3D_DECISIONS.md`). Steps 0–10 proceed without further approval between steps; a genuine technical blocker stops only the affected step with evidence and the decision required. Step 11 needs separate authorization.**

## 1. Guiding constraints
* Work in a **new working copy** of the delivered M3c tree (commit `b7bf26044b49…`, tree `f3f51e7cc7a4…`), never in the accepted trees or deliverable directories (`/home/claude/work/m3c/deliver` is read-only for this work).
* M3c is not reopened. Its code, tests, docs and pinned hashes are baseline. A defect found in M3c is **reported and decided**, never patched in passing.
* Additive changes only. Local git commits on a new branch (`m3d-baseline` from the M3c commit). **No push, no remote.**
* Commit trailers: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01Tn5sFaYestnwwCAFno8kea`.
* Real-recording validation and decoder licensing review stay outstanding and are never claimed.
* Report actual test results; distinguish executed from specified.

## 2. Standard gate (applies to every step; a step is not done until all hold)
| Gate | Condition |
|---|---|
| G1 | Accepted suites (Phase 1/2, M3a, M3b, M3c) pass **unmodified** in both venvs; counts ≥ baseline (ros2 1704/6; plain 1603/107; plus the M3c suite) |
| G2 | PRES hash pins verified (Phase 1/2, M3a, M3b rev2, M3c) and help-golden tests green |
| G3 | The step's new tests green in both venvs (executed counts reported) |
| G4 | The step's mutants killed (≥95 % non-equivalent; every survivor justified; must-kill rows 100 %) |
| G5 | `git diff --name-status` against the previous step shows only the files named in the step; `cli.py` only in Step 8, additive only |
| G6 | Determinism spot-check (DET-01 subset) green |
| G7 | Local commit with the step message; no push |
| G8 | Findings that contradict the design (e.g. an onset-table mismatch) are written to the step report and **stop the step** pending a decision; nothing is adapted silently |

## 3. File-level changes
### 3.1 New (package `src/physicore_collector/localization/`)
`__init__.py`, `intervals.py`, `symptoms.py`, `dependencies.py`, `blockers.py`, `precedence.py`, `consistency.py`, `chains.py`, `report.py`, `service.py` (responsibilities: DESIGN §2.3), plus `vocabulary.py` (closed enumerations: labels, statuses, blocker/caveat codes, hypothesis templates, discriminating-evidence phrases, wording templates, forbidden terms).
### 3.2 New tests/tools/docs
`tests/localization/` (`test_int.py`, `test_sym.py`, `test_pre.py`, `test_blk.py`, `test_dep.py`, `test_con.py`, `test_chn.py`, `test_prv.py`, `test_unc_wrd_rpt.py`, `test_cli.py`, `test_det.py`, `test_scl.py`, `test_adv.py`, `test_preservation.py`), `tests/localization/oracle.py`, `fixtures/` (L1 builders, L2 scenarios), `tools/{replay_verifier,bench,mutate,mutants_step1..9,req_matrix}.py`, `examples/localization/{dependencies.example.yaml,README}`, `docs/M3D_LOCALIZATION.md`, `docs/M3D_TEST_REPORT.md`.
### 3.3 Edits to accepted files
| File | Edit | When | Approval |
|---|---|---|---|
| `cli.py` | one additive command `evidence localize`, lazy import, no deleted line | Step 8 only | D2 |
| everything else | **none** | — | — |

## 4. Steps and acceptance gates
### Step 0 — Working copy and baseline record
* Copy the M3c tree to a new directory; create branch `m3d-baseline`; record in `STEP0_BASELINE.md`: M3c commit and tree ids, deliverable hashes, test counts in both venvs, `cli.py` hash, help goldens for all accepted commands (captured from the M3c tree), importable public APIs M3d relies on (`anomaly.service.run_anomaly_detection`, `anomaly.findings` models, `anomaly.clock`, investigator public models).
* **Feasibility checks (executed, reported):** (a) every accessor M3d needs is public (else: blocker + decision, no patching); (b) the exact list of categories and fields each M3c detector emits (feeds Step 2); (c) M2 investigator can be run in-process on the same inventory; (d) `yaml.safe_load` behaviour with anchors/tags/depth (feeds DEP-14/20).
* Gate: G1, G2; no source change. Commit: baseline record only.

### Step 1 — `vocabulary.py`, `intervals.py`, report primitives
* Files: `vocabulary.py`, `intervals.py`, base of `report.py` (statement model, canonical JSON, forbidden-term check), tests INT-*, RPT-02..05, WRD-01 on synthetic strings, DET-06 static checks, PRES-01..11 harness, oracle for intervals.
* Mutants: MUT-INT (≥20).
* Gate: G1–G7. Property tests seeded and reproducible.

### Step 2 — Symptom normalisation
* Files: `symptoms.py`; fixtures for all M3c categories (L2), M2 adapter.
* **First action:** verify the onset table (DESIGN §4.1) against the fields actually emitted by each detector (SYM-01..13, SYM-20 drift guard). Any mismatch ⇒ stop (G8), report, request decision.
* Tests: SYM-*; PRV-01/04 on symptoms; replay verifier skeleton (no M3d imports).
* Mutants: MUT-SYM (≥25).
* Gate: G1–G7 and an explicit **onset-table verification record** (table row × detector field × result).

### Step 3 — Dependency file loader and graph
* Files: `dependencies.py`, `examples/localization/dependencies.example.yaml`.
* Tests: DEP-01..28 (hostile suite), DET-04 hash invariance, CON-17/18 binding resolution (L1).
* Mutants: MUT-DEP (≥25).
* Gate: G1–G7; fuzz run (DEP-13) 500 seeds green; DEP-14/20 bounded time recorded.

### Step 4 — Precedence, layers, earliest
* Files: `precedence.py`.
* Tests: PRE-*, UNC-04, PRE-16 scale run; oracle comparison.
* Mutants: MUT-PRE (≥25).
* Gate: G1–G7; oracle agreement on all random instances.

### Step 5 — Blockers and caveats
* Files: `blockers.py`; defence decision wired into `precedence`.
* Tests: BLK-*, ADV-05..08, UNC-03; verify M2 blocker rule against actual M2 output (BLK-10).
* Mutants: MUT-BLK (≥25).
* Gate: G1–G7; blocker exact-count fidelity (BLK-17) green.

### Step 6 — Consistency and links
* Files: `consistency.py`.
* Tests: CON-*, ADV-01..04, ADV-09, ADV-12..13, ADV-17, UNC-05.
* Mutants: MUT-CON (≥25).
* Gate: G1–G7; CON-16 oracle agreement (1,000 instances); link-cap path (CON-20) green.

### Step 7 — Components, chains, hypotheses, localization
* Files: `chains.py`.
* Tests: CHN-* (incl. CHN-23..30 bounded enumeration), ADV-14..16, ADV-18, ADV-26..30, SCL-09..10, WRD-05 on hypothesis text, SCL-03/04 (iterative, no recursion).
* Mutants: MUT-CHN (≥25).
* Gate: G1–G7; CHN-20..22 property tests green.

### Step 8 — Service, full report, CLI (the only accepted-file edit)
* Files: `service.py`, completed `report.py` (readable renderer), `cli.py` (**additive command only**).
* Tests: CLI-*, RPT-*, WRD-*, PRV-02/03/05..12, ADV-19..25, PRES-03/04/05 (diff shows additive `cli.py` only; help goldens of every other command byte-identical).
* Mutants: MUT-PRV, MUT-WRD, MUT-CLI (≥20 each).
* Gate: G1–G7; exit-code table fully exercised; PATH-unchanged check (CLI-04) green.

### Step 9 — Replay verifier completion, adversarial suites, performance
* Files: `tools/replay_verifier.py` (complete), `tools/bench.py`, remaining ADV, SCL-01..08, DET-01..08 full, PRV-11 on the delivered M3c example.
* Deliverable of this step: measured benchmark numbers (time, peak RSS, bytes/symptom) recorded honestly; any miss against NFR-06 is **documented, not tuned away and not hidden**.
* Mutants: MUT-DET (≥20); re-run all earlier families to catch interactions.
* Gate: G1–G7; requirement matrix script (`req_matrix.py`) shows no empty row.

### Step 10 — Full mutation run, documentation, test report
* Files: `docs/M3D_LOCALIZATION.md` (user guide: how to write a dependency file, what each label means and does not mean, worked example), `docs/M3D_TEST_REPORT.md` (actual counts per venv, executed vs specified, mutation table with killer per mutant, benchmark, outstanding limitations).
* Full mutation run in the background (no source edits while running); full suites in both venvs run sequentially (a known flaky accepted test, day5 sigterm, is reported if it recurs under load and re-run in isolation, never hidden).
* Gate: G1–G7; every FR/NFR/PR traced; wording scan of all docs.

### Step 11 — Delivery (separate authorization required)
Built only after review and an explicit instruction: ZIP from the exact final commit, patch against the M3c commit, example package, SHA-256 file, verification record (ZIP integrity; patch applies to the M3c baseline; patched tree == HEAD == ZIP; pins verified; tests from the extracted ZIP match). Any verification failure stops and is reported; nothing is modified to force a pass. Nothing pushed.

## 5. Risk register
| Risk | Mitigation |
|---|---|
| Onset table disagrees with detector fields | Step 2 verification first; stop-and-decide (G8) |
| A public accessor needed from `anomaly`/`investigator` is missing | Step 0 check; blocker + decision; no patching of accepted code |
| Interval ordering too conservative to be useful on real data (wide gap intervals make everything `undetermined`) | This is intended behaviour; documented; real-recording validation remains outstanding and is the right place to tune *declarations*, never thresholds in code |
| Users read chains as causes | closed vocabulary, forbidden-term test, scope sentence in every report, `head` not `origin` |
| Hostile dependency file / names | strict loader, size and depth limits, inert text, hostile suites |
| Memory/time at 100k symptoms | measured in Step 9, reported honestly (M3c's memory miss is the precedent) |
| Mutation harness runtime | per-step mutant lists, run in background; final full run in Step 10 |
| Scope creep into M3e (human report) or cross-clock alignment | explicit non-goals (DESIGN §1.2); any such request is a new decision |

## 6. Estimated size (indicative, reported against actuals at the end)
~2,000–2,600 lines of source in `localization/`; ~330+ tests; ~220+ mutants. These are planning numbers, not acceptance criteria.

## 7. Decisions needed before Step 0
D1–D11 (DESIGN §9) with the recommended answers, plus confirmation that **M1 = Phase 1 collector, M2 = Phase 2 inventory/investigator**.
