# M4e record: report, view, CLI command

Built: `rootcause/investigate.py` (investigation report, strict schema, exit codes 0/1/2/3), `rootcause/view.py` (Markdown and static HTML from one block list; no script, no external resource), CLI command `evidence hypotheses`, independent parity verifier `tests/rootcause/tools/parity_m4.py`, examples in `examples/rootcause/`, guide `docs/M4_HYPOTHESES.md`.

## Tests run (real runs, logs in `m4_records/logs/`)
- Plain environment, full suite: 3207 passed, 110 skipped.
- ros2 environment, full suite: 3389 passed, 7 skipped.
- Mutation run on `investigate.py` and `view.py`: 61 mutants; 45 killed at first, 16 survivors. Tests added (`test_investigate_more.py`, INV_14 to INV_20); all 16 re-run and killed (second log plus a one-mutant rerun for "catalogue invalid accepted", which was killed after the message assertion was added). No equivalents left.
- The `digest_mismatch` branch cannot be reached by any dev scenario (none leaves a generated check pending), so INV_16 injects the check result; this is stated in the test.

## Disclosures
- Command name: the plan's `evidence investigate` is already owned by M2, so the new command is `evidence hypotheses`.
- My own pins PRES_05 and PRES_06 were updated to allow two added commands. One line of the accepted `tests/localization/test_preservation.py` was widened (the loop tuple now `("system-model", "hypotheses")`), because its help-golden otherwise fails on the new row.
- HTML is static only. M3e timeline components are not reused; there is no JS layer.
- The M4 delivery package (zip, patch, checksums) is not built; it needs separate authorization.
- Not done: the hold-out run (kept uninspected until the end). All 12 catalogue entries remain `proposed`; sign-off (D12) and the wording table in `docs/M4_CAUSAL.md` are still the user's. Real-recording validation and decoder licensing clearance remain outstanding.
