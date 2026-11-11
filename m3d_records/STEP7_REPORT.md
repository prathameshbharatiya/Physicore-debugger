# Step 7 report: chains, hypotheses, localization (chains.py)

## Files
- New: `src/physicore_collector/localization/chains.py`, `tests/localization/test_chn.py` (68 tests), `tests/localization/tools/mutants_step7.py` (77 mutants).
- Edited (M3d files only): `consistency.py` (components now include singletons, FR-CHN-01; `analyse_links` accepts a shared `WorkBudget`), `vocabulary.py` (`ALLOWED_TOKENS`: the approved label and the three approved template names are exempt from the forbidden-wording check; `HYPOTHESIS_SENTENCES`), `tests/localization/oracle.py` (recursive maximal-path oracle), `test_con.py` / `mutants_step6.py` (component expectations updated for singletons).
- No M3c source or test touched.

## Behaviour
- Chains: maximal paths of the per-lane link DAG (lanes never mixed). Lazy iterative DFS in deterministic order (lane, head by onset lower/upper/id, successors likewise); only the first `chains_reported` are kept, one more is pulled to tell an exactly-full report from a truncated one. Total by DP over V+E (exact big integer) only when V+E <= `count_work_limit`, else `chains_total` null, `bounded_unknown`, `chains_lower_bound` = enumerated count. One work budget (`path_work_limit`) is shared with the Step 6 path enumeration; each vertex visit costs one unit.
- Blockers raised: `chains_truncated` (count = exact suppressed, or null when the total is unknown), `path_work_limit_reached`. Affected components (those with a head not fully enumerated) and their chains become `not_defended` with the blocker id; unaffected components keep their status (CHN-29). Link/path-level blockers make every component and chain `not_defended`.
- Hypotheses: exactly three templates. Template 1 is per link (`consistent_with` / `excluded_by_declared_edge_timing` / `not_excluded`, both intervals, edge ids, window shown). Templates 2 and 3 per chain, always `not_excluded`, ordered by template name, facts exact (declared ancestors of the head node not on the chain, their symptoms strictly before the head, detector coverage; caveat codes and recording blockers), `discriminating_evidence_needed` from the fixed vocabulary.
- Localization: per component and lane, locus = nodes of layer 0, frontier = nodes of the last layer; unlocalized = symptoms on nodes with no incident declared edge; without a dependency file only topics and layers (`topic_layers`).
- No recursion anywhere in `localization/` (static AST scan SCL-04).

## Interpretation made (design ambiguity, recorded)
- CHN-01 requires a linear A -> B -> C to be one chain although a `via_nodes` link A -> C exists. A `via_nodes` arc is therefore left out of the chain DAG when its own declared path is already walked by consistent arcs through an observed intermediate symptom (X -> Z and Z -> Y both exist). Arcs through unobserved intermediates stay. This is a local reduction, not a full transitive reduction.
- Output bounds fixed in code and always shown next to exact totals: at most 50 ancestors listed per hypothesis, 20 symptom ids per ancestor.
- Chain `defence` is inherited from its component.

## Verification (actual runs)
- test_chn.py: 68 passed. DP total vs recursive brute force on 500 seeded DAGs; reported chains are a canonical prefix of the oracle enumeration on 300 DAGs; maximality, completeness and acyclicity properties; determinism across shuffles and three `PYTHONHASHSEED` values; 10,000 and 100,000 deep chains; 2^20 chains with 1,001 pulled and fewer than 50,000 node visits; 4^12 layered DAG and 2^60 diamond total exact; count-limit cases `bounded_unknown`.
- Mutation: 77 mutants, 76 killed, 1 justified equivalent ("iter: every head marked done"). First run 57 killed; tests added and two invalid mutants repaired and rerun (both killed).
- Full suites: ros2 2241 passed, 6 skipped, 1 failed; plain 2139 passed, 108 skipped, 1 failed.

## Known isolated failure
`test_PRES_09_no_accepted_module_mentions_the_anomaly_package_before_step_9` still fails in both venvs (lexical false positive from `localization/` importing the M3c package; test unmodified, no exemption added). The suite is therefore NOT clean.

## Limitations
- Scale benchmarks (SCL-01/02/05..08) are Step 9; only SCL-03/04/09/10 ran here and no timing claims are made.
- The Step 6 per-node BFS for counters is not budgeted (see STEP6_REPORT).
- Service, report assembly, renderer and CLI are Step 8.
- Commit: 23522c5
