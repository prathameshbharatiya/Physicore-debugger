# M4c record: evidence ledger, checks, ordinal tiers

Branch `m4-baseline`, built on M4b (ed318c3). Local only; nothing pushed.

## What was built
- `rootcause/checks.py`: check planning and running. A check is answered by an M3d M2 symptom, or by a generated M2 contract (value ranges from declared `value_range` constraints; hashed; run through the accepted investigator only if the recording's digests equal the report's). Otherwise `not_runnable` / `not_run` with a reason. Silence in the M3d report is never read as a pass.
- `rootcause/evaluate.py`: evidence ledger (observed, inferred, assumption, unconfirmed; stance supports/contradicts/neutral/missing), prediction states, tiers (contradicted, not_assessed, supported, possible), deterministic order, leader rule (first is supported, share at least 1/2, ahead of every other non-contradicted instance by at least 1/3, no blocker), assumption register, sensitivity (repeat without each declared source), and the adapter to the M4-0 harness input.
- `rootcause/instantiate.py`: one additive field, `slot_selects`, on each instance.
- `docs/M4_EVALUATION.md`; tests `test_evaluate.py`, `test_evaluate_more.py`; mutants `tests/rootcause/tools/mutants_m4c.py`.
- No accepted test, CLI or pinned deliverable changed.

## Design decisions found by running the corpus
- First version gave a wrong leader on all three F08 scenarios (declared edge direction contradicted by the recorded order). Fix: a neighbour prediction counts only through the M3d link label between the two symptoms.
- A log symptom made the crash entry "supported" by restating itself. Fix: predictions about the unit's own symptom topics are neutral.
- Consequence: on the development corpus, leaders appear only where a check or a consistent link supplies direct evidence (F03). Most scenarios end `competing` or `unresolved`. That is the intended conservative behaviour, not a defect, but it means few leaders. F01 stays unresolved because the M3d link is undetermined when the downstream symptom lies inside the silence interval (known M3d limit).

## Results actually run
- Development corpus (30 scenarios, harness with real scores): confident wrong leaders 0 of 30; correct abstention 9 of 9; truth in set 21 of 21; required missing evidence named in all scenarios that require it; wording violations 0; forbidden-link violations 0. This shows the software follows its definitions, not accuracy on real robots. The hold-out corpus has not been run or inspected.
- Mutation: 69 of 70 killed; 1 documented equivalent. Log: `logs/m4c_mutation.log`.
- Full suite, plain env: 3129 passed, 110 skipped, 0 failed (SCL_07 passed this time). `logs/m4c_plain.log`.
- Full suite, ros2 env with browser shim: 3311 passed, 7 skipped, 0 failed. `logs/m4c_ros2.log`. (An earlier ros2 run died with the process and was rerun from the start.)

## Not built, by design, and disclosed
- Plausibility against a declared rate constraint (no v1 entry makes a rate prediction).
- Check windows from chain intervals (range checks run over the whole recording).
- Event-response and other M2 kinds are not compiled from the model, which holds no trigger or response values; they use M3d symptoms only.
- Claim ladder and causal wording (M4d); report and page (M4e); the one-time hold-out run (end of M4).

## Open
- All 12 catalogue entries remain `proposed` pending your sign-off. Real-recording validation and decoder licensing remain outstanding. Delivery needs separate authorization.
