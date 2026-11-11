# Step 6 report: consistency, links, components (consistency.py)

## Files
- New: `src/physicore_collector/localization/consistency.py`, `tests/localization/test_con.py` (65 tests), `tests/localization/tools/mutants_step6.py` (65 mutants).
- Edited (M3d files only): `localization/blockers.py` (added `CHAIN_LEVEL_CODES`; link/path/chain blockers never downgrade a lane's earliest-symptom claim; `link_cap_reached` detail now reads "A link or path limit was reached; some links were not considered."), `tests/localization/oracle.py` (independent brute-force link oracle). `m3d_records/STEP5_REPORT.md` moved into this directory.
- No M3c source or test touched; `git diff b7bf260 HEAD -- src` lists only `localization/` files.

## Behaviour
- Truth table of DESIGN 4.4 in `classify`; window compared with `sep_min` (boundary `== W` consistent, `W+1` outside); touching/equal bounds `undetermined`.
- One link per (X, Y, lane, declared path). Multi-edge window = sum only if every edge declares one. Link model re-derives label and separations and refuses inconsistent content; each link carries two observed statements, one derived-order statement, one declared statement per edge, and the notes `declared_edge_unverified` (always) / `path_via_unobserved_nodes`.
- Intermediate nodes report symptoms strictly between X and Y and detector coverage (`covered` only if every topic of the node was covered by a detector that ran; otherwise `not_covered`, never "unaffected").
- Undeclared pairs: never linked or enumerated; `no_declared_relation_count` (counted per lane, different nodes, no declared path either way) and `cross_lane_pairs_not_compared` are computed arithmetically.
- Bounds: link cap with exact suppressed count (`link_cap_reached`, source `m3d_limit`); paths-per-pair and depth cuts (`link_cap_reached`, count = node pairs); shared global work budget (`path_work_limit_reached`, count = node pairs not examined; a half-searched pair contributes no links).
- Components: weakly connected components of `consistent_with_propagation` links (union-find, iterative); `not_defended` when links are incomplete, a member is truncated, or a lane blocker applies.

## Verification (actual runs)
- test_con.py: 65 passed. CON-16: 1,000 seeded instances agree with the naive oracle (links, labels, separations, no-declared counter); more than 300 instances had links.
- Mutation: 65 mutants, 64 killed, 1 justified equivalent ("ana: cross-lane pairs linked", union of lanes only adds lanes with an empty product). First run 53 killed; tests added for the rest; two redundant guards removed from the code instead of tested.
- Full suites: ros2 2173 passed, 6 skipped, 1 failed; plain 2071 passed, 108 skipped, 1 failed.

## Known isolated failure (not a Step 6 defect)
`tests/anomaly/test_preservation.py::test_PRES_09_no_accepted_module_mentions_the_anomaly_package_before_step_9` fails in both venvs. Characterised: the only files outside `anomaly/` and `cli.py` containing the word are four `localization/` files (intervals, symptoms, vocabulary, blockers), all through legitimate imports of the M3c package or its types (`AnomalyReport`, `anomaly_category`). No other file offends and no accepted file changed. The test is unmodified and unbypassed; an exemption needs a separate decision. The suite is therefore NOT clean.

## Limitations / deviations
- Ancestors, chains, hypotheses, localization and the chain-level halves of ADV-01..03/17 are Step 7.
- Reachability for the counters is a per-node BFS (worst case nodes x edges = 25M steps at the 5,000/5,000 limits); it is not covered by the work budget.
- `no_declared_relation_count` counts pair-lane instances; a pair sharing two lanes counts in each.
- `inconsistent_order` links are shown with the downstream interval as `earlier` in the order statement.
- Commit: 5144163
