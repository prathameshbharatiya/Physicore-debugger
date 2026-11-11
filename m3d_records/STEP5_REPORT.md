# Step 5 report: blockers and caveats (blockers.py)

- New: `src/physicore_collector/localization/blockers.py`, `tests/localization/test_blk.py` (54 tests), `tests/localization/tools/mutants_step5.py` (66 mutants). Commit b24075b.
- Clarification A: every blocker has a source record (kind, ref, field) and artifact digests. Observation references appear only where the source holds one: `observations_truncated` (the finding's own retained observations) and `m2_blind_spot_before_or_unplaced` (M2 blind-spot observations resolved to an inventory artifact; an unresolvable id is dropped, never invented). Inventory, detector, counter and limit blockers are metadata-only.
- Fixed templates only; standing gap codes (e.g. `capture_health_unavailable`) are not blockers; a clean recording yields none.
- M2 rule is lane-scoped, stricter than M2's time-scoped defence (BLK-10b records this); cross-checked against actual M2 output (BLK-10).
- Mutation: 66 killed, 0 survived (first run 45 killed; tests added in two rounds).
- Full suites: ros2 2108 passed, 6 skipped, 1 failed; plain 2006 passed, 108 skipped, 1 failed. Both failures are the open G1 conflict (PRES_09, test unmodified).
