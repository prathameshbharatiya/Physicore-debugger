# M4-0 record: baseline, plan lock, ground-truth corpus

Branch `m4-baseline`, local only, from the M3e commit `1b609789f45c58c4f6230cc97c3e151178a439ce`. No push, no remote change. Decisions D1 to D12 of the plan were taken as recommended (the user said to start building; they were not answered one by one).

## What was built

| Commit | Content |
|---|---|
| be4ce3f | manifest of 436 accepted files (`tests/rootcause/golden/M4_BASE_FILES.sha256`); `tests/rootcause/test_pres.py` (PRES_01 to 11); planned allow-list edits; empty package `rootcause` |
| 56fd18e | frozen scenario specification (`tests/rootcause/corpus/spec.json`, `SCENARIOS.md`), pinned by `test_spec.py` |
| c24f395 | spec amendment A1 (bounded silences), made before any catalogue entry existed |
| 5ed6dd7 | seeded corpus generator `corpus/generate.py` and `test_corpus.py` (CORP_01 to 11) |
| 5f1ba60 | evaluation harness `corpus/harness.py` and `test_harness.py` (HAR_01 to 15) |

Corpus: 10 families, 30 development scenarios (seeds 1 to 3) and 20 hold-out scenarios (seeds 101, 102, topics under `/ns1` and `/ns2`). Every development scenario was run through the accepted M3c detectors and M3d localization, and each shows exactly the symptoms the spec lists (CORP_06). The hold-out is generated only with an explicit flag; its digest is pinned by hash and its symptoms were never run or inspected.

## Edits to accepted tests (planned, additive)

| File | Edit |
|---|---|
| `tests/localization/test_preservation.py` | 3 lines added: M4 path prefixes and one import-rule skip for the `rootcause` package |
| `tests/localization/test_unc_pres.py` | 1 line added: ignore added files under `examples/rootcause/` and `docs/M4_` |
| `tests/investigation_view/test_pres.py` | 1 line added (prefixes); 2 lines widened (the view-import rule skips `rootcause`; the PRES_13 pin ignores lines ending `# M4`) |

The last file is the one exception to "pure additions": two existing lines were widened, not only added to. PRES_04 pins this exact shape. Baseline copies of the three files and of `cli.py` as of the M3e commit are in `m4_records/`. No source file outside `tests/`, `m4_records/` and the new package changed. `cli.py` is unchanged (PRES_06).

## Results

| Environment | Result | Log |
|---|---|---|
| ros2 (with browser shim) | 3074 passed, 7 skipped, 0 failed | `logs/m4_0_ros2.log` |
| plain | 2892 passed, 110 skipped, 0 failed | `logs/m4_0_plain.log` |

Both are the M3e totals (3032 and 2850) plus 42 new tests. A first ros2 run without the browser shim skipped the JS module (2994 passed, 8 skipped); it is not the number reported. The corpus reproduces byte for byte in both environments (CORP_01, CORP_03). The harness was checked with 9 hand-made mutants; all 9 were caught. No full mutation run was done for M4-0.

## Findings about what is already built (reported, nothing adapted silently)

1. **A source that stops for good is invisible to M3c.** The publishing-gap detector evaluates only the interval between two captured messages; boundary silence is never evaluated. The silent-source families therefore use a bounded silence (spec amendment A1). This is a real product limit and belongs in every M4 document.
2. **The rate-change baseline is a median.** If the degraded windows are half or more of the evaluated windows, the baseline degrades with them and nothing is flagged. The generator starts the degradation late (message 62 to 70) so that it stays a minority.
3. **M3d cannot order a bounded silence against a downstream symptom that falls inside it.** In F01, F07 and F08 the one link has the label `undetermined`; in F02 it is `consistent_with_propagation`. M4 must handle `undetermined` links honestly; this will be a hard, realistic case in the corpus.
4. **M3d forbids some words as report keys** (`rank`, `score`, `probability`, `confidence`, `root_cause`, `likelihood`, `weight`). The M4 report is a separate schema, but D3 (ordinal tiers) should name its fields `tier` and `leading`, not `rank` or `score`, unless a later plan change releases a word.
5. **Event-response needs an M2 contract.** F03 supplies one; without it no symptom of that kind exists.

## Not done, not claimed

- Real-recording validation (M3a) and decoder licensing clearance remain outstanding.
- The harness takes a "score input" (schema 1); the adapter from the real M4 report does not exist yet and is built with M4c and M4d.
- No wording ladder exists yet, so the harness's `released` table is empty: every forbidden word is a violation at every level.
- No accuracy or success-rate claim of any kind. The corpus is synthetic and written by the same author as the planned catalogue.
- M4-0 delivery (zip, patch, checksums) has not been made; it needs separate authorization.

## Gate

| Gate item | Result |
|---|---|
| corpus and harness reproduce byte for byte | yes: CORP_01, CORP_03, HAR_13 (two hash seeds, locale C) |
| specification committed before the catalogue exists | yes: commit 56fd18e; no catalogue module exists at HEAD |
