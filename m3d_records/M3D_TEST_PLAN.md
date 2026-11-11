# M3d — Acceptance Test Plan (Revision 1 — APPROVED with clarifications A and B)

Status: **APPROVED. Tests are specified here; a test counts as executed only when the step report says so with actual counts.** Revision 1 adds PRV-13..16 (clarification A) and CHN-23..30, ADV-26..30, SCL-09..10 (clarification B); CHN-09 and CHN-16 and SCL-02 are amended as marked. Real-recording validation is a separate, still-outstanding requirement and is not claimed by anything here.
Rules inherited from M3c: every test has **exactly one** expected behaviour (variants get their own IDs); oracles are written independently of the code under test; fixtures are synthetic; fixtures needing "different artifacts" follow FX-ART (real bytes, real recomputed digests, integrity never relaxed).
IDs: `<family>-<nn>`. "Covers" cites `M3D_REQUIREMENTS.md`.

## 1. Fixtures and oracles
* **Two fixture levels.** (L1) *Symptom-level*: hand-built `Symptom` objects (declarative spec: id, node, lane, onset interval, blockers) fed directly to precedence/consistency/chains — fast, exhaustive, used for the combinatorial and property tests. (L2) *Recording-level*: synthetic MCAP built with the M3c builder (`tests/anomaly/fixtures`) so that the **real M3c detectors** produce the findings; used for SYM, PRV, CLI, ADV end-to-end. L2 scenarios: `cascade_linear` (A→B→C with known gaps), `fanout`, `diamond`, `feedback_cycle`, `decoy_earlier_undeclared`, `two_domains`, `incomplete_recording`, `truncated_findings`, `blind_detector`, `hostile_names`, `clean_recording`.
* **Independent oracle** (`tests/localization/oracle.py`): a deliberately naive O(n²) reference for interval order, layers, earliest, edge labels and link enumeration, written from DESIGN §4 with no shared code. Production results are compared to it on every L1 case and on random instances (seeded, seed recorded in test ID).
* Standard assertions on every L2 test: report validates against the strict model; replay verifier (§11) passes; wording check passes; no float in report; exit code as specified.

## 2. INT — interval algebra (L1)
| ID | Case | Expect | Covers |
|---|---|---|---|
| INT-01 | `Interval(domain, 5, 4)` | rejected (lower>upper) | FR-INT-01 |
| INT-02 | float, bool, str, None bounds | rejected | FR-INT-01 |
| INT-03 | Compare intervals of different domains (`precedes`, `overlaps`, `min_separation`) | `ClockDomainError`, each separately | FR-INT-01 |
| INT-04 | `[1,2] ≺ [3,4]` | true | FR-INT-02 |
| INT-05 | `[1,3] ≺ [3,4]` (touching) | false (unordered) | FR-INT-02 |
| INT-06 | `[1,3] ≺ [4,5]` | true (strict gap of one ns) | FR-INT-02 |
| INT-07 | point `[3,3]` vs point `[3,3]` | unordered both ways | FR-INT-02 |
| INT-08 | point `[3,3]` vs `[4,4]` | `3 ≺ 4`, not reverse | FR-INT-02 |
| INT-09 | Property (2,000 seeded random interval triples): irreflexive | holds | FR-INT-02 |
| INT-10 | Property: asymmetric (`X≺Y ⇒ ¬Y≺X`) | holds | FR-INT-02 |
| INT-11 | Property: transitive (`X≺Y ∧ Y≺Z ⇒ X≺Z`) | holds | FR-INT-02 |
| INT-12 | Property: exactly one of `X≺Y`, `Y≺X`, `unordered` | holds | FR-INT-02 |
| INT-13 | Property (interval-order axiom, Fishburn): `X≺Y ∧ Z≺W ⇒ X≺W ∨ Z≺Y` | holds | FR-INT-02 |
| INT-14 | `sep_min/sep_max` for `[0,10]` and `[30,50]` | 20 / 50 | FR-INT-03 |
| INT-15 | int64 max, uint64 max, 2^63 + 1 bounds | exact results, no overflow, type `int` | FR-INT-03 |
| INT-16 | `sep_min` for overlapping intervals | ≤ 0 reported, label undetermined downstream | FR-INT-03 |
| INT-17 | Time-shift invariance: adding a constant to every bound leaves order and separations unchanged (property) | holds | FR-INT-02/03 |

## 3. SYM — symptom normalisation (L2 with real M3c detectors, L1 for edge cases)
One test per **onset-table row** against **every field the real detector emitted** (this is the Step-2 verification of DESIGN §4.1; any mismatch fails the step and requires a decision).
| ID | Case | Expect | Covers |
|---|---|---|---|
| SYM-01 | `ros_log` WARN run (3 msgs) | one symptom, point onset at first message, extent first–last, basis `received_message` | FR-SYM-02 |
| SYM-02 | `diagnostics` ERROR | point onset, identity = status name (inert) | FR-SYM-02 |
| SYM-03 | `goal_status` ABORTED | point onset | FR-SYM-02 |
| SYM-04 | `battery` health fault | point onset | FR-SYM-02 |
| SYM-05 | `publishing_gap` between t=1.0 s and t=3.5 s | onset `[1.0 s, 3.5 s]`, basis `silence_between_captured_messages`, note `capture_loss_indistinguishable_from_silence` | FR-SYM-02 |
| SYM-06 | `rate_change` window `[ws, we]` | onset = window span, basis `window_span` | FR-SYM-02 |
| SYM-07 | `non_finite` run | point at first message of run, extent kept | FR-SYM-02 |
| SYM-08 | `array_length` change | point onset | FR-SYM-02 |
| SYM-09 | `stamp_order` regression | log-lane symptom at receipt time **and** secondary payload-stamp lane symptom | FR-SYM-06 |
| SYM-10 | Primary-lane `stamp_order` symptom is never ordered against the payload-stamp lane symptom | no link, no layer relation across lanes | FR-SYM-06 |
| SYM-11 | M2 divergence (`transition`) given `--expectations` | symptom in M2 domain, basis `received_sample` | FR-SYM-05 |
| SYM-12 | M2 `event_response` deadline expiry | onset `[trigger, expiry]`, caveat "response may be late" carried | FR-SYM-05 |
| SYM-13 | No `--expectations` | no `m2_finding` symptoms exist | FR-SYM-05 |
| SYM-14 | Severity/notes/coverage copied: deep-equality to source for 40 random findings | identical | FR-SYM-03 |
| SYM-15 | Flip severity of one finding (L1) | order, layers, earliest unchanged | FR-SYM-03 |
| SYM-16 | `observations_truncated` source | `truncated=true`, blocker `observations_truncated` | FR-SYM-04 |
| SYM-17 | One-to-one: N findings ⇒ N symptoms, ids unique, source ids unique | holds | FR-SYM-01 |
| SYM-18 | `symptom_id` stable under `PYTHONHASHSEED` and finding-order shuffle; changes when onset or parameters change | as stated | FR-SYM-01 |
| SYM-19 | Symptom limit lowered to 5 with 12 findings | first 5 in canonical order, suppressed 7, blocker `symptom_cap_reached` | FR-SYM-07 |
| SYM-20 | Table-drift guard: parametrised over every category string M3c can emit (`all_detectors()` ⇒ categories); a category with no onset rule | test fails (forces a design decision) | FR-SYM-02 |
| SYM-21 | Unknown category injected (L1) | refused with a problem; not defaulted | FR-SYM-02 |

## 4. PRE — layers and earliest (L1, oracle comparison)
| ID | Case | Expect | Covers |
|---|---|---|---|
| PRE-01 | Empty lane | `none_observed` | FR-PRE-02 |
| PRE-02 | Single symptom, no blockers | layer 0, earliest = it, `defended` | FR-PRE-02 |
| PRE-03 | Chain A≺B≺C | layers 0,1,2 | FR-PRE-01 |
| PRE-04 | Two disjoint earliest (A,B both layer 0) | `tied`, both listed | FR-PRE-02 |
| PRE-05 | A=`[0,10]`, B=`[5,6]` overlapping | one layer, `tied` | FR-PRE-02 |
| PRE-06 | A=`[0,5]`, B=`[5,9]` (touching) | unordered, `tied` | FR-INT-02 |
| PRE-07 | Layer property: symptom in layer k has a ≺-predecessor in layer k−1 (k>0) and none in layer ≥k | holds (property) | FR-PRE-01 |
| PRE-08 | Interval-order non-trivial: A=`[0,1]`, B=`[0,100]`, C=`[50,60]`; A≺C, B unordered with all | layer(A)=0, layer(B)=0, layer(C)=1 | FR-PRE-01 |
| PRE-09 | Random 200 instances × 3 sizes vs naive oracle: layers identical | equal | FR-PRE-01 |
| PRE-10 | Input order shuffle (property) | identical result | FR-PRE-01 |
| PRE-11 | Two lanes, one symptom each | two earliest, statement "unordered across lanes" | FR-PRE-04 |
| PRE-12 | Defended statement text equals the fixed scope sentence | exact | FR-PRE-03 |
| PRE-13 | Single earliest but a blocker in the lane | `not_defended` with blocker ids | FR-PRE-02 |
| PRE-14 | Single earliest, `truncated=true` | `not_defended` | FR-PRE-02 |
| PRE-15 | Add an unrelated symptom later in time (property) | earliest unchanged | FR-PRE-02 |
| PRE-16 | 100,000 symptoms single lane | completes; layers equal oracle on a 2,000-symptom subsample (oracle too slow for full) | FR-PRE-01 |

## 5. BLK — blockers and caveats
| ID | Case | Expect | Covers |
|---|---|---|---|
| BLK-01 | `recording_incomplete` (missing segment) | blocker on every lane; exit 1 | FR-BLK-01 |
| BLK-02 | Enabled detector with `blind_spot` on configured topic | blocker `detector_blind_spot`, scope = detector+topic | FR-BLK-02 |
| BLK-03 | Enabled detector `insufficient_evidence` | blocker `detector_insufficient_evidence` | FR-BLK-02 |
| BLK-04 | Detector disabled (`--detector` selection excludes it) | caveat `detector_not_configured`, **not** a blocker; scope stated | FR-BLK-02 |
| BLK-05 | Detector `not_applicable` (type mismatch) | caveat `detector_not_applicable`, not a blocker | FR-BLK-02 |
| BLK-06 | Undecodable messages counter > 0 | blocker `undecodable_messages`, exact count | FR-BLK-01 |
| BLK-07 | Unparseable messages | blocker `unparseable_messages`, exact count | FR-BLK-01 |
| BLK-08 | M3c findings cap hit (limit lowered) | blocker `findings_truncated` with exact suppressed count | FR-BLK-01 |
| BLK-09 | M2 expectation not evaluated | blocker `m2_expectation_not_evaluated` | FR-BLK-01 |
| BLK-10 | M2 blind spot before/unplaced | blocker `m2_blind_spot_before_or_unplaced` (same rule as M2's defence test, cross-checked against M2 output) | FR-BLK-01 |
| BLK-11 | Topic-scoped blocker (detector blind on topic T); the lane's single earliest symptom is on unrelated topic U | **fail closed**: lane earliest is `not_defended`; the blocker keeps its topic scope T in the report (variants: BLK-11a gap detector blind, 11b rate detector blind, 11c undecodable on T, 11d M2 blind spot on T) | FR-BLK-04 |
| BLK-12 | Blocker present in lane A only | lane B stays `defended` | FR-BLK-04 |
| BLK-13 | Always-present caveats appear: `receipt_order_not_occurrence_order`, `real_recording_validation_outstanding` | present on a clean recording | FR-BLK-03 |
| BLK-14 | Caveat never changes defence (property: removing all caveats leaves defence identical) | holds | FR-BLK-03 |
| BLK-15 | Gap present ⇒ `capture_loss_indistinguishable_from_silence` | present | FR-BLK-01 |
| BLK-16 | `boundary_gaps_not_evaluated` carried from the M3c gap result | present | FR-BLK-01 |
| BLK-17 | Blocker exact-count fidelity: every count equals the source count (parametrised) | equal | FR-BLK-01 |

## 6. DEP — dependency file (hostile-input heavy)
| ID | Case | Expect | Covers |
|---|---|---|---|
| DEP-01 | Minimal valid file | loads, semantic hash defined | FR-DEP-01 |
| DEP-02 | Unknown key at each level (top, node, binding, edge): four tests | problem, exit 3 | FR-DEP-01 |
| DEP-03 | Missing `declared_by` / empty / whitespace-only | problem | FR-DEP-01 |
| DEP-04 | Duplicate edge id | problem | FR-DEP-01 |
| DEP-05 | Self-loop `from == to` | problem | FR-DEP-01 |
| DEP-06 | Endpoint neither `/topic` nor a declared node | problem | FR-DEP-01 |
| DEP-07 | `relation` outside the closed set | problem | FR-DEP-01 |
| DEP-08 | `window_ns`: 0, −1, 1.5, "5", true, null-with-key | problem each (6 tests) | FR-DEP-01 |
| DEP-09 | `window_ns` absent | valid | FR-DEP-01 |
| DEP-10 | Binding to unknown node / unsupported identity kind / duplicate binding | problem each | FR-DEP-01 |
| DEP-11 | Empty `edges` | problem | FR-DEP-01 |
| DEP-12 | Bad YAML syntax; bad JSON; non-mapping top level; empty file; binary file | problem, no exception (5 tests) | FR-DEP-02 |
| DEP-13 | Loader never raises: fuzz 500 seeded random byte/structure mutations of a valid file | every call returns `(graph, problems)` with exactly one of the two non-empty; no exception | FR-DEP-02 |
| DEP-14 | YAML anchors/aliases (billion-laughs), explicit tags (`!!python/object`), merge keys | refused, bounded time (<2 s) | FR-DEP-03 |
| DEP-15 | File of 1 MiB + 1 byte; exactly 1 MiB valid | refused / accepted | FR-DEP-03 |
| DEP-16 | 5,001 edges refused; 5,000 accepted | as stated | FR-DEP-03 |
| DEP-17 | 5,001 nodes refused; 5,000 accepted | as stated | FR-DEP-03 |
| DEP-18 | String of 257 chars; of 256 chars | refused / accepted | FR-DEP-03 |
| DEP-19 | Control chars (`\x00`, `\x1b`, `\n` in id, RTL override U+202E) in ids/names | refused | FR-DEP-03 |
| DEP-20 | Nesting depth 10,000 in YAML/JSON | refused with a problem (no RecursionError) | FR-DEP-03, NFR-08 |
| DEP-21 | Symlink / FIFO / directory / missing path as dependency file | problem, exit 3 | NFR-08 |
| DEP-22 | Semantic hash invariant: edge order, key order, YAML vs JSON, whitespace/comments | equal | FR-DEP-05 |
| DEP-23 | Semantic hash changes with each of: edge added/removed, window, relation, binding, `declared_by`, direction reversed (6 tests) | differs | FR-DEP-05 |
| DEP-24 | Cycle `A→B→A` | accepted | FR-DEP-06 |
| DEP-25 | Path enumeration depth 32 reached on a 40-node chain | stops at 32; `depth_truncated` flagged; total reported as unknown (`exact_total` null), never estimated (clarification B) | FR-DEP-06 |
| DEP-26 | 10,000-path-per-pair bound and the global work budget on dense layered graphs | per-pair cap: 10,000 paths found, total unknown (`exact_total` null); global budget: total node-visits never exceed `path_work_limit`, exhausted budget stays exhausted across pairs; enumeration order canonical (dst, edge id) (clarification B) | FR-DEP-06 |
| DEP-27 | Static check: no code path derives an edge from topic names, types or recording content | AST test + behavioural test (two topics with obviously related names and no edge ⇒ no link) | FR-DEP-04 |
| DEP-28 | Ancestors/paths on a 5,000-node chain | iterative, correct | NFR-03 |

## 7. CON — edge consistency (L1, oracle comparison)
| ID | Case | Expect | Covers |
|---|---|---|---|
| CON-01 | `X≺Y`, no window | `consistent_with_propagation` | FR-CON-01 |
| CON-02 | `X≺Y`, window W, `sep_min == W` | consistent | FR-CON-01 |
| CON-03 | same, `sep_min == W+1` | `outside_declared_window` | FR-CON-01 |
| CON-04 | `Y≺X` | `inconsistent_order` | FR-CON-01 |
| CON-05 | overlapping | `undetermined` | FR-CON-01 |
| CON-06 | touching (`X.upper == Y.lower`) | `undetermined` | FR-CON-01 |
| CON-07 | Gap symptom X on U with interval wide enough to overlap Y on V | `undetermined` (wide interval) | FR-CON-01 |
| CON-08 | Symptoms in different lanes on connected nodes | no link; counted in `cross_lane_pairs_not_compared` | FR-CON-01 |
| CON-09 | Two-edge path both windows 100/200 | window 300 | FR-CON-02 |
| CON-10 | Two-edge path with one window missing | window null | FR-CON-02 |
| CON-11 | `via_nodes`: intermediate node with symptom between | listed in `symptom_ids_between` | FR-CON-03 |
| CON-12 | intermediate node with no symptom and **no** enabled detector covering it | `coverage: not_covered` | FR-CON-03 |
| CON-13 | intermediate node with no symptom but covered by an enabled detector | `coverage: covered`, `symptom_ids_between: []` | FR-CON-03 |
| CON-14 | Undeclared pair (same lane, ordered, related names) | no link; `no_declared_relation_count` incremented | FR-CON-04 |
| CON-15 | Reverse the declared edge direction (metamorphic) | every consistent link becomes `inconsistent_order`/`undetermined`; none stays consistent unless intervals overlap | FR-CON-01 |
| CON-16 | Property: 1,000 seeded random graph+symptom instances vs naive oracle | labels and separations equal | FR-CON-01 |
| CON-17 | Binding resolves symptom node (logger name ⇒ node) | link formed on the node | FR-DEP-01 |
| CON-18 | Binding with near-miss identity (case/whitespace/prefix difference) | not bound | FR-DEP-01 |
| CON-19 | Edge whose endpoint has no symptoms | no link; coverage `no_symptom_on_endpoint` | FR-CON-04 |
| CON-20 | Link cap lowered | exact suppressed count; blocker `link_cap_reached` | FR-DEP-06 |

## 8. CHN — components, chains, hypotheses
| ID | Case | Expect | Covers |
|---|---|---|---|
| CHN-01 | Linear A→B→C symptoms, all consistent | one component, one chain, head A | FR-CHN-01/02 |
| CHN-02 | Fan-out A→B, A→C | one component, two maximal chains sharing head | FR-CHN-02 |
| CHN-03 | Diamond A→B→D, A→C→D | one component, two chains, no double counting of D in components | FR-CHN-01/02 |
| CHN-04 | Feedback cycle in dependencies (A↔B) with strictly increasing times | link DAG acyclic; chains terminate | FR-CHN-02 |
| CHN-05 | Two independent incidents | two components | FR-CHN-01 |
| CHN-06 | Singleton symptom | own component, no chain, in `unlocalized` if node has no edges | FR-CHN-01/06 |
| CHN-07 | Chain head is named `head`; word "origin" absent anywhere | holds | FR-CHN-02 |
| CHN-08 | 10,000-symptom single chain | iterative; no `RecursionError`; correct | FR-CHN-02 |
| CHN-09 | Chains cap lowered to 3 with 10 chains (V+E within count limit) | the deterministic first 3 reported; `chains_total` = 10, status `exact`; blocker `chains_truncated` with suppressed 7 | FR-CHN-02 |
| CHN-10 | Exactly three hypothesis templates, per link/chain as specified; any other template name rejected by the model | holds | FR-CHN-03 |
| CHN-11 | Templates 2 and 3 are `not_excluded` on every chain in every fixture (parametrised) | holds | FR-CHN-03 |
| CHN-12 | Hypotheses order is by template name only (no plausibility order); property: permuting facts does not change order | holds | FR-CHN-03 |
| CHN-13 | Template 1 `excluded_by_declared_edge_timing` for an `inconsistent_order` pair shows both intervals, edge id and window | present | FR-CHN-04 |
| CHN-14 | Template 2 facts: declared common ancestor listed; "had symptoms before head" computed; detectors covering it listed | exact | FR-CHN-03 |
| CHN-15 | Ancestor with **no** edge declared | not listed; `discriminating_evidence_needed` includes "a declared edge for the ancestor" | FR-CHN-03 |
| CHN-16 | Chain with capped links | `not_defended`, blocker ids listed |
| CHN-23 | Enumeration is deterministic: the first 1,000 chains identical across hash seeds, edge-order shuffles, finding-order shuffles | identical | FR-CHN-07 |
| CHN-24 | Lazy enumeration: with 10^6 maximal chains, memory/time bounded by the report limit (instrumented counter shows ≤ budget node-visits; no list of all chains ever built — checked by a materialisation probe) | holds | FR-CHN-07 |
| CHN-25 | DP total equals brute-force enumeration on 500 seeded random small DAGs | equal | FR-CHN-07 |
| CHN-26 | Count limit lowered below V+E | `chains_total` null, status `bounded_unknown`, `lower_bound` = enumerated count, never a number presented as total | FR-CHN-07 |
| CHN-27 | Report limit reached, count exact | `chains_truncated` blocker with exact suppressed count; affected components `not_defended` | FR-CHN-07 |
| CHN-28 | Work budget exhausted mid-enumeration | `path_work_limit_reached` blocker; chains found so far reported; claims for affected components `not_defended`; `chains_total_status` per count rule | FR-CHN-07 |
| CHN-29 | Unaffected component in the same run keeps its status when another component is truncated | stays as computed | FR-CHN-07 |
| CHN-30 | Property (seeded): reported chains are a prefix, in canonical order, of the full enumeration for small DAGs | holds | FR-CHN-07 | FR-CHN-05 |
| CHN-17 | Chain inside a lane with a blocker | `not_defended` | FR-CHN-05 |
| CHN-18 | Locus / frontier / unlocalized for a 3-layer component | exact nodes | FR-CHN-06 |
| CHN-19 | `discriminating_evidence_needed` strings all from the fixed vocabulary (parametrised over all fixtures) | holds | FR-CHN-03 |
| CHN-20 | Links are strictly forward in time ⇒ DAG: property over 1,000 random instances | holds | FR-CHN-02 |
| CHN-21 | Chain maximality: no chain is a proper subpath of another (property) | holds | FR-CHN-02 |
| CHN-22 | Chain completeness: every consistent link lies on ≥1 chain | holds | FR-CHN-02 |

## 9. PRV — provenance (L2 + replay verifier)
`tests/localization/tools/replay_verifier.py` is test-only, extends the M3c verifier, and **does not import any M3d module** (it reads the JSON report and the raw MCAP with the independent reader).
| ID | Case | Expect | Covers |
|---|---|---|---|
| PRV-01 | Every symptom's evidence refs resolve to (artifact, topic, index, log_time) in the raw artifact | all resolve | PR-01/02 |
| PRV-02 | Every link/chain/hypothesis reaches ≥1 observation through statements; every blocker reaches its source record (PRV-13/14) | all resolve | PR-01 |
| PRV-03 | Model refuses to serialise a statement with no reference (negative, per record type: 5 tests) | validation error | PR-01 |
| PRV-04 | Onset interval for each symptom recomputed independently from the raw recording by the verifier | equal | PR-02 |
| PRV-05 | Every `derived_order` statement re-derived from the two intervals in the report | rule satisfied | PR-02 |
| PRV-06 | Every `declared` statement cites dependencies sha256, semantic hash, edge id, declared_by; values equal the file's | equal | PR-03 |
| PRV-07 | No statement of kind other than the three (parametrised over report walk) | holds | PR-03 |
| PRV-08 | A `declared` relation never appears under `observed` (metamorphic: edit only the dependency file ⇒ no `observed` statement changes) | holds | PR-03 |
| PRV-09 | Tamper test: modify one byte in the artifact ⇒ evidence integrity failure ⇒ exit 1 and verifier reports mismatch | as stated | PR-02 |
| PRV-10 | Report digests equal digests recomputed from the files | equal | PR-01 |
| PRV-11 | Replay on the **delivered M3c example package** (`m3c-example.zip`) | passes (a baseline cross-check) | PR-02 |
| PRV-13 | Observation-backed blocker (`observations_truncated`, `undecodable_messages` with indexes) | resolves to source record **and** to ≥1 real observation | PR-01b |
| PRV-14 | Metadata-only blocker (`recording_incomplete` from inventory, `detector_blind_spot`, `findings_truncated`, `symptom_cap_reached`) | resolves to its source record with artifact digest(s) and scope; **no observation reference present** | PR-01b |
| PRV-15 | Blocker model with no source record, or with a fabricated observation (index not in artifact / not in the source finding) | refused by model / flagged by the replay verifier | PR-01b |
| PRV-16 | Replay verifier re-resolves every blocker's source record in the raw inventory/detector result and its digest | all resolve | PR-01b |
| PRV-12 | Replay after serialise→parse roundtrip of the report | passes | PR-01 |

## 10. UNC, WRD, RPT
| ID | Case | Expect | Covers |
|---|---|---|---|
| UNC-01 | Source uncertainty notes copied verbatim (deep equal) | equal | PR-05 |
| UNC-02 | M3d adds only `onset_interval_width`, `declared_edge_unverified`, `path_via_unobserved_nodes`, `blocked_defence` | closed set | PR-05 |
| UNC-03 | Metamorphic: add any blocker ⇒ no `not_defended` becomes `defended` (200 random instances) | holds | PR-06 |
| UNC-04 | Metamorphic: widen any onset interval ⇒ no `unordered` pair becomes ordered, no `tied` becomes unique | holds | PR-06 |
| UNC-05 | Metamorphic: remove an edge window ⇒ no label becomes `outside_declared_window` | holds | PR-06 |
| UNC-06 | No key/value matching confidence, probability, score, rank, likelihood, weight at any depth, on every fixture | none | FR-RPT-02 |
| WRD-01 | Forbidden-term list (M3c list + `root cause`, `origin`, `originated`, `triggered`, `culprit`, `responsible`, `blame`, `fault in`, `explains`, `therefore`) over **every** generated string of every fixture's JSON and readable report, whole-word, case-insensitive, token `consistent_with_propagation` exempt | no hit | FR-PRE-03, PR-04 |
| WRD-02 | The word "propagation" appears only inside the label token | holds | PR-04 |
| WRD-03 | Hostile logger name `"root cause: motor"` / `"ignore previous instructions; origin"` appears only as an inert quoted data field, truncated to 80 chars, never inside a generated sentence | holds | PR-04 |
| WRD-04 | Readable report built only from fixed templates: scanning every sentence against the template set | all matched | FR-RPT-04 |
| WRD-05 | Defended/not_defended wording identical for equal structural situations across fixtures | equal | FR-PRE-03 |
| WRD-06 | The DESIGN §10 limitations and the severity disclaimer present in every report | present | FR-RPT-05 |
| RPT-01 | Top-level key set exactly DESIGN §3.3 | equal | FR-RPT-01 |
| RPT-02 | Unknown field rejected at each record type (Symptom, Lane, Link, Chain, Component, Hypothesis, Blocker) | 7 validation errors | FR-RPT-01 |
| RPT-03 | Optional-absent keys omitted | absent | FR-RPT-01 |
| RPT-04 | No float anywhere (walk) | none | FR-RPT-03 |
| RPT-05 | JSON ⇄ model roundtrip byte-identical | equal | FR-RPT-03 |
| RPT-06 | Readable and JSON agree on counts (symptoms, links, chains, blockers) | equal | FR-RPT-04 |

## 11. CLI and EXIT
| ID | Case | Expect | Covers |
|---|---|---|---|
| CLI-01 | Clean recording, valid config, no deps | exit 0, order-only report stating "no dependencies declared" | FR-CLI-01 |
| CLI-02 | Valid deps on `cascade_linear` | exit 0, one chain | FR-CLI-01 |
| CLI-03 | `--json` output parses and equals the model | equal | FR-CLI-01 |
| CLI-04 | PATH tree and all input files hash-identical before/after (also with `--json`) | unchanged | NFR-07 |
| CLI-05 | Missing `--anomaly-config` | exit 2 (usage) | FR-CLI-02 |
| CLI-06 | Invalid anomaly config | exit 3, no analysis | FR-CLI-02 |
| CLI-07 | Invalid expectations | exit 3 | FR-CLI-02 |
| CLI-08 | Invalid dependency file | exit 3, problems listed | FR-CLI-02 |
| CLI-09 | Invalid dependency file **and** unreadable evidence | exit 3 (precedence) | FR-CLI-03 |
| CLI-10 | Unreadable evidence, valid inputs | exit 2, `evidence_not_inspectable` | FR-CLI-02 |
| CLI-11 | Incomplete recording | exit 1, report produced | FR-CLI-02 |
| CLI-12 | Incomplete recording with blocked defences | exit 1 (not 0) | FR-CLI-02 |
| CLI-13 | Blocked defence alone (complete recording) | exit 0 | FR-CLI-02 |
| CLI-14 | `--detector` unknown id | exit 2 usage, message lists valid ids | FR-CLI-01 |
| CLI-15 | `--detector` subset | non-selected detectors in caveats as not configured | FR-BLK-02 |
| CLI-16 | `--fail-on-findings` / `--fail-on-chains` given | usage error exit 2 (option absent) | FR-CLI-04 |
| CLI-17 | `--help` text lists exactly the documented options | golden | FR-CLI-01 |
| CLI-18 | `exit_reasons` lists every applicable reason in precedence order | equal | FR-CLI-02 |
| CLI-19 | Dependency file given as `--dependencies -` (stdin) | usage error (not supported) | FR-CLI-01 |
| CLI-20 | Evidence path is the M3c example ZIP's extracted tree | runs, exit as the inventory dictates | FR-CLI-01 |

## 12. DET — determinism
| ID | Case | Expect | Covers |
|---|---|---|---|
| DET-01 | Same inputs under `PYTHONHASHSEED` 0, 1, random (subprocess ×3) | byte-identical `--json` | NFR-01 |
| DET-02 | Different cwd | identical | NFR-01 |
| DET-03 | Shuffle M3c finding order (L1) | identical | NFR-01 |
| DET-04 | Shuffle edge order and node order in the dependency file | identical report incl. hash | NFR-01, FR-DEP-05 |
| DET-05 | Same content in a different tmp dir name | differs **only** by `artifact_ref`-derived ids (same rule as M3c); with the same dir name identical | NFR-01 |
| DET-06 | Static AST scan of `localization/`: no `float(`, no float literal, no `time`/`datetime` wall-clock use, no `random`, no `set`/`dict` iteration feeding output without `sorted` | none | NFR-02 |
| DET-07 | Two runs in one process (state leakage) | identical | NFR-01 |
| DET-08 | Locale/timezone env variation (`LC_ALL`, `TZ`) | identical | NFR-01 |

## 13. SCL — scale and performance (measured, reported, not assumed)
| ID | Case | Expect | Covers |
|---|---|---|---|
| SCL-01 | 100,000 symptoms, 5,000 nodes, 5,000 edges (L1) | completes; time and peak RSS recorded by `tools/bench.py` | NFR-06 |
| SCL-02 | 200,000 links cap reached | exact suppressed count; blocker; time recorded |
| SCL-09 | Work-counter instrumentation: total node-visits ≤ `path_work_limit` on every ADV-26..29 instance | holds | FR-CHN-07 |
| SCL-10 | DP count on 5,000-node/5,000-edge DAG (big integers) | completes; time recorded | FR-CHN-07 | FR-DEP-06 |
| SCL-03 | Chain depth 10,000 and 100,000 | no recursion error | NFR-03 |
| SCL-04 | Static check: no recursive function in `localization/` (call-graph scan) except those explicitly bounded and listed | none | NFR-03 |
| SCL-05 | Scaling check: time(2n)/time(n) for n = 10k, 20k, 40k, 80k symptoms | ratio ≤ 3 (near-linear/n log n; the bound is stated in advance and a miss is reported, not tuned away) | NFR-06 |
| SCL-06 | Memory per symptom measured at 100k (RSS delta / count), added to the M3c figures | reported (a SHOULD, never claimed met before measurement) | NFR-06 |
| SCL-07 | Pathological: all symptoms overlapping (one giant layer, 100k) | linear-ish, no quadratic blow-up | NFR-06 |
| SCL-08 | Pathological: complete bipartite dependency (1,000 × 1,000 edges blocked by 5,000 edge limit → file refused) | refused | FR-DEP-03 |

## 14. ADV — adversarial scenarios (L2 unless stated)
Each is one expected behaviour; every case asserts the **absence of an overclaim** in addition to the specific result.
| ID | Case | Expect | Covers |
|---|---|---|---|
| ADV-01 | **Decoy earlier symptom on an undeclared node**: `/unrelated` WARN at t=1 s, true cascade A→B starts t=5 s | lane earliest is the decoy; **chain head is A**; decoy is `unlocalized`; no link from decoy | FR-CON-04, FR-CHN-06 |
| ADV-02 | Decoy on a **declared** node later than cascade | no change to chain | FR-CHN-02 |
| ADV-03 | Reversed edge declaration (B→A though A fails first) | `inconsistent_order`; template-1 `excluded_by_declared_edge_timing`; templates 2,3 still `not_excluded`; no chain A→B | FR-CON-01, FR-CHN-04 |
| ADV-04 | Symptom with huge interval (gap) spanning two cascade members | `undetermined`, not ordered | FR-CON-01 |
| ADV-05 | Detector blind on the cascade's true upstream topic; downstream symptoms observed | downstream chain `not_defended`; blocker names the upstream topic | FR-BLK-02, FR-CHN-05 |
| ADV-06 | Upstream topic **not configured** (not `blind_spot`) | caveat only; wording states "no detector covered X", never "X healthy" | FR-BLK-02, FR-CON-03 |
| ADV-07 | Truncated upstream findings (`findings_truncated`) | blocker; earliest `not_defended` | FR-BLK-01 |
| ADV-08 | Incomplete recording (segment lost mid-cascade) | exit 1; blockers; no defended claim | FR-BLK-01 |
| ADV-09 | Mixed clock domains: M2 expectation in `header_stamp` domain and M3c log-time symptoms with *numerically* interleaved values | separate lanes; no link across; numeric coincidence ignored | FR-SYM-06, FR-CON-01 |
| ADV-10 | Hostile logger/diagnostic names: 10 kB, `\x00`, newline, ANSI escape, `root cause`, template-injection `{0}` `%s` `${x}` | inert truncated label; wording check passes; JSON valid | PR-04 |
| ADV-11a | Node id or topic in the dependency file containing a unicode confusable or U+202E | refused with a problem | NFR-08 |
| ADV-11b | Same hostile topic name present in the **recording** (not the dependency file) | carried as inert truncated data; wording check passes | NFR-08 |
| ADV-12 | Timestamps at 0, 1, 2^62, 2^63−1, 2^64−1 | exact ordering and separations; no overflow | FR-INT-03 |
| ADV-13 | Equal onset times on A and B with declared A→B | `undetermined` (ties not ordered) | FR-CON-01 |
| ADV-14 | Receipt order inverted relative to causality (B received before A though A causes B; modelled by two recorder lanes with queue delay) | `inconsistent_order` plus caveat `receipt_order_not_occurrence_order`; no claim A did not cause B | FR-CON-01, PR-06 |
| ADV-15 | Fully connected declared graph over 30 topics, one symptom each at distinct times | link cap and path bounds respected; output bounded and exact counts | FR-DEP-06 |
| ADV-16 | Self-referential feedback: symptom A at t1 and again at t3, B at t2 | links A1→B and B→A3 only; no cycle chain | FR-CHN-02 |
| ADV-17 | A dependency file that "explains" a coincidence (edge declared between two unrelated topics that happen to fail in order) | link is `consistent_with_propagation`; `declared_edge_unverified` note present; templates 2 and 3 `not_excluded` | PR-03, PR-05 |
| ADV-18 | The same symptom set with and without the edge (metamorphic) | the only differences are links, chains, hypotheses, components; layers/earliest/blockers identical | FR-DEP-04 |
| ADV-19 | Clean recording with a dependency file | no symptoms; exit 0; "no symptoms observed by the enabled detectors" (not "healthy") | FR-BLK-02 |
| ADV-20 | `--expectations` that has a contract for a topic whose M3c detectors are off | symptoms only from M2; M3c scope caveat present | FR-SYM-05 |
| ADV-21 | Two valid recordings concatenated as two segments with a time reset | M3c domain/ordering rules apply; M3d inherits blockers; no cross-segment chain claim beyond receipt-time order | FR-BLK-01 |
| ADV-22 | Prompt-injection text in an identity field, rendered in the readable report | appears quoted and truncated only | PR-04 |
| ADV-23 | Dependency file claims `declared_by` containing a control char or 300 chars | refused with a problem (exit 3) | FR-DEP-03 |
| ADV-24 | Dependency file lists the same pair via different ids and relations (duplicate edges, different windows) | both accepted; path window rule uses per-edge window; link lists both edge ids | FR-CON-02 |
| ADV-26 | Dense layered DAG, 12 layers × 4 nodes fully connected between layers (≈4^12 chains) | completes within budget; first 1,000 chains reported; `chains_total` exact via DP (big integer) or `bounded_unknown` per limits; never enumerated in full | FR-CHN-07 |
| ADV-27 | Exponential diamond chain (k = 60 diamonds, 2^60 chains) | completes; total exact via DP; reported ≤ 1,000 | FR-CHN-07 |
| ADV-28 | Same with `count_work_limit` below V+E | `bounded_unknown`; no estimate | FR-CHN-07 |
| ADV-29 | Many (U,V) pairs each below the per-pair 10,000-path limit whose product exceeds the global budget | `path_work_limit_reached`; total work counter ≤ budget | FR-CHN-07, FR-DEP-06 |
| ADV-30 | Truncated enumeration never upgrades: same instance with budget raised ⇒ no previously `defended` claim becomes `not_defended` and vice versa only as explained by blockers (metamorphic) | holds | FR-CHN-07, PR-06 |
| ADV-25 | Node name collides with a topic string (`/odom` declared as a node id) | refused: node ids may not start with `/` | FR-DEP-01 |

## 15. PRES — preservation and isolation (run every step)
| ID | Case | Expect | Covers |
|---|---|---|---|
| PRES-01 | Accepted suites run unmodified in both venvs | counts ≥ baseline (ros2 1704 passed/6 skipped; plain 1603 passed/107 skipped, plus the 532 M3c tests collected in the M3c tree) | NFR-04 |
| PRES-02 | SHA-256 of every Phase 1/2, M3a, M3b rev2, **and M3c** accepted file equals the recorded pin (M3c pins from `M3C_DELIVERABLES.sha256` and the M3c commit tree) | equal | NFR-04 |
| PRES-03 | `git diff --name-status <M3c commit>` shows only added files under the allowed paths plus `cli.py` | holds | NFR-04 |
| PRES-04 | `cli.py` diff has no deleted/modified existing line (purely additive) | holds | NFR-04 |
| PRES-05 | `--help` of every accepted command byte-identical to goldens captured from the M3c tree | equal | NFR-04 |
| PRES-06 | Phase 2/M3a/M3b/M3c example outputs byte-identical to the delivered ones | equal | NFR-04 |
| PRES-07 | Import-isolation (NFR-05) enforced by AST scan of all packages | holds | NFR-05 |
| PRES-08 | Plain venv (no ros2 extras): `import physicore_collector.localization` and the CLI help work; localize on a JSON-only fixture works | works | NFR-05 |
| PRES-09 | No new entry in `pyproject.toml` dependencies | unchanged | NFR-04 |
| PRES-10 | `cdr.py` remains the only importer of the third-party decoder (grep + AST) | holds | NFR-05 |
| PRES-11 | M3c replay verifier and M3c mutation harness still pass unchanged on the new tree | pass | NFR-04 |

## 16. MUT — mutation testing
Same harness as M3c (`tests/localization/tools/mutate.py`, textual mutants on a copy of `src`; mutants listed in reviewable `mutants_stepN.py`; run in the background; **no edits to `src` while running**). Each mutant is paired with the tests expected to kill it; an *equivalent* mutant must be justified in writing and removed from the denominator; a survivor that is not equivalent means the **tests** are strengthened, never the mutant list thinned.
Covers NFR-09. Target: ≥ 95 % of non-equivalent mutants killed per module; 100 % of the mutants in the "must-kill" rows below.
| Family | Module | Must-kill mutants (examples; the full list is part of each step) |
|---|---|---|
| MUT-INT | intervals | `<` → `<=` in `precedes`; swap lower/upper in separation; drop domain check; use `max` for `min`; `+1` off-by-one in `sep_min` |
| MUT-SYM | symptoms | onset for gap uses `t_after` only; rate onset ignores window start; severity influences sort; drop `truncated` propagation; swap `received_message`/`window_span`; stamp-order lane merged into log lane |
| MUT-PRE | precedence | layer = count of predecessors instead of longest chain; earliest ignores blocker; ties collapse to first; `defended` when `len==2`; sort unstable on equal lower bounds; ignore `truncated` |
| MUT-BLK | blockers | disabled detector treated as blocker; `blind_spot` ignored; scope widened/narrowed; exact count replaced by capped count; caveat promoted to blocker; `findings_truncated` dropped |
| MUT-DEP | dependencies | accept unknown key; allow self-loop; window `0` allowed; bool accepted as int; skip alias refusal; semantic hash includes order; infer edge from topic name; depth bound off-by-one |
| MUT-CON | consistency | `sep_min <= W` → `<`; `inconsistent_order` and `undetermined` swapped; window sum treats missing as 0; cross-lane comparison allowed; undeclared pair linked; reversed edge direction |
| MUT-CHN | chains | recursion replaced (limit test); non-maximal chains emitted; cycle allowed; head chosen as last; template 2/3 set to `excluded`; hypothesis ranking inserted; locus uses last layer |
| MUT-PRV | report/provenance | drop `declared_by`; observed/declared kind swapped; evidence refs truncated to one; digest from path not content; statement without reference allowed |
| MUT-WRD | report | forbidden-term check disabled; hostile text interpolated into a sentence; "origin" wording introduced; score field added |
| MUT-CLI | service/cli | precedence 3>2>1 permuted; exit 1 swallowed when blocked; deps file read after analysis; `--fail-on` accepted |
| MUT-DET | all | iteration over a `set` without `sorted`; float division introduced; time-based tiebreak |
Mutant-count floor: ≥ 20 per module, ≥ 220 total (M3c had 306); the final report lists killed / survived / equivalent with the killing test of each mutant.

## 17. Metamorphic and property summary (cross-cutting)
Time-shift invariance (INT-17), permutation invariance (PRE-10, DET-03/04), adding an unrelated symptom/edge changes nothing about unrelated results (PRE-15, ADV-18), blockers only downgrade (UNC-03), wider intervals only reduce ordering (UNC-04), removing windows only removes `outside_declared_window` (UNC-05), reversing edges only removes consistency (CON-15). All use seeded generators; the seed is part of the test ID and printed on failure.

## 18. Gates for the test suite itself
* Every FR/NFR/PR ID in `M3D_REQUIREMENTS.md` appears in ≥1 "Covers" cell; a script emits the matrix and fails on an empty row.
* No test expectation offers alternatives ("A or B"); no test is `xfail` or silently skipped (a skip must name its reason and appear in the report count).
* Test-count floor (stated in advance, reported against actuals): ≥ 330 localization tests; ≥ 12 property tests (seeded); ≥ 25 adversarial L2 tests.
* Test report lists **executed** vs **specified-only** separately, the actual counts, the mutation table, the benchmark numbers, and all outstanding limitations.

## 19. What this plan does not do
It does not validate against real robot recordings, does not clear decoder licensing, does not establish that declared graphs are correct, and does not assert that any chain reflects physical causality.
