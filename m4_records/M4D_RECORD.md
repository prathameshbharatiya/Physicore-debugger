# M4d record: claim ladder and causal layer

Branch `m4-baseline`, built on M4c (555374e). Local only; nothing pushed.

## What was built
- `rootcause/causal.py`: the claim ladder (levels 0 to 4; level 5 not reachable), a fixed wording table keyed by level, the gate that refuses a stronger word than a level allows, level computation with caps (blocker, open or not runnable check, direct contradiction) and reasons, baseline comparability (detector configuration, dependency file, parameters), baseline and declared-intervention contrast, propagation facts read from the M3d edge coverage, "what would raise it" lines, and diagnostic asks for sets without a leader.
- `rootcause/evaluate.py`: one additive field (`link_id`) on ledger items that rest on a consistent link.
- `docs/M4_CAUSAL.md`; tests `tests/rootcause/test_causal.py` (36); mutants `tests/rootcause/tools/mutants_m4d.py`.
- No accepted test, CLI or pinned deliverable changed.

## Found and fixed during the build
- Two preservation tests failed on the first full run: the word "requests" (a key name) tripped the no-network pin, and the literal "anomaly" in a key name tripped the no-M3c-mention pin. Fixed in the new module (key renamed `diagnostic_asks`; detector configuration compared as "all report inputs other than the dependency file"). The accepted tests were not touched.
- A level-2 rule that only counted the satisfied share would have let a unit with an unmet prediction reach level 2. It now requires that no prediction is unmet.

## Results actually run
- Mutation: 66 of 68 killed; 2 documented equivalents. `logs/m4d_mutation.log`.
- Full suite, plain env: 3165 passed, 110 skipped, 0 failed. `logs/m4d_plain.log`.
- Full suite, ros2 env with browser shim: 3347 passed, 7 skipped, 0 failed. `logs/m4d_ros2.log`.
- Development corpus with levels: wording violations 0, confident wrong leaders 0 of 30, correct abstention 9 of 9. Levels reached on it: 0 and 2 only. Levels 1, 3 and 4 are shown reached, and stopped short, by synthetic tests (a reached and a stopped-short example for every level, plus attempts to push a hypothesis above its level: a single satisfied check, a coincidental order, no baseline, an incomparable baseline, an intervention after the symptoms). Synthetic results show the software follows its definitions, not accuracy on real robots.

## Open
- The wording table (`docs/M4_CAUSAL.md`) is a proposal. The plan says M4d waits for your wording table; it needs your sign-off.
- Propagation facts are reported next to each unit but do not change a tier (no v1 entry is keyed to an upstream origin). Shared-origin analysis between chains is not built.
- All 12 catalogue entries remain `proposed`. Real-recording validation and decoder licensing remain outstanding. Delivery needs separate authorization.
