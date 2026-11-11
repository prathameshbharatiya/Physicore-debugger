# M3d — Engineering Requirements (Revision 0, for review)

Status: **DESIGN ONLY. Nothing here is implemented or executed.** Companion to `M3D_DESIGN.md` (rationale, methodology) and `M3D_TEST_PLAN.md` (verification). Every requirement has an ID and is verified by named test families (column "Verified by"). A requirement with no test is a defect in this document.
Keywords: MUST / MUST NOT are binding; SHOULD is a goal that must be reported on if unmet (as the M3c memory budget was).

## 1. Functional requirements

### 1.1 Symptom normalisation (FR-SYM)
| ID | Requirement | Verified by |
|---|---|---|
| FR-SYM-01 | Every M3c finding MUST map to exactly one symptom; every symptom MUST name its source kind and source id. No finding is dropped or merged silently. | SYM, PRV |
| FR-SYM-02 | Onset interval and `onset_basis` MUST follow the table in DESIGN §4.1, verified against the fields each detector actually emits (a mismatch is reported, never adapted). | SYM, PRES |
| FR-SYM-03 | Severity, notes, coverage, identity and observation references MUST be copied from the source unchanged; severity MUST NOT influence ordering, grouping or selection. | SYM, ADV, MUT |
| FR-SYM-04 | A truncated source finding MUST produce `truncated=true` and a blocker. | SYM, BLK |
| FR-SYM-05 | M2 divergences MUST be included only when `--expectations` is given, each in the lane of its own time domain. | SYM, CLI |
| FR-SYM-06 | Payload-stamp symptoms MUST create a secondary lane only for `stamp_order` findings (D11); lanes MUST NOT be compared with each other. | SYM, ADV |
| FR-SYM-07 | Symptom count above the symptom limit MUST yield the first N in canonical order, the exact suppressed count, and a `symptom_cap_reached` blocker. | SCL, BLK |

### 1.2 Intervals and precedence (FR-INT / FR-PRE)
| ID | Requirement | Verified by |
|---|---|---|
| FR-INT-01 | `Interval` MUST be integer-nanosecond, `lower <= upper`, one clock domain; mixed-domain operations MUST raise. | INT |
| FR-INT-02 | `X ≺ Y` iff `X.upper < Y.lower` in the same domain; equal instants MUST be unordered. The relation MUST be irreflexive, asymmetric and transitive (property-tested). | INT |
| FR-INT-03 | Separations `sep_min = Y.lower − X.upper`, `sep_max = Y.upper − X.lower` MUST be exact integers with no overflow at int64/uint64 boundaries (Python ints; no numpy). | INT, ADV |
| FR-PRE-01 | Layers MUST be defined as the longest `≺`-chain ending at the symptom, minus one; computation O(n log n); result independent of input order. | PRE, DET |
| FR-PRE-02 | Earliest set = layer 0 per lane. `defended` iff exactly one member AND no blocker in the lane AND the member is not truncated; otherwise `not_defended`, `tied` (≥2 members) or `none_observed`. | PRE, BLK |
| FR-PRE-03 | A defended earliest MUST carry the fixed scope sentence (DESIGN §4.2) and MUST NOT be worded as cause, origin or trigger. | PRE, WRD |
| FR-PRE-04 | The report MUST state that earliest symptoms of different lanes are unordered across lanes. | PRE |

### 1.3 Blockers and caveats (FR-BLK)
| ID | Requirement | Verified by |
|---|---|---|
| FR-BLK-01 | Blocker set MUST be exactly the classes in DESIGN §4.3 plus `chains_truncated` and `path_work_limit_reached`, each with code, scope, exact count (where computable), detail and a reference to its actual source record. | BLK |
| FR-BLK-02 | A detector that is disabled, not configured or not applicable MUST appear as scope (`detector_not_configured` / `detector_not_applicable`), never as "nothing happened"; an **enabled** detector with `blind_spot` or `insufficient_evidence` on configured scope MUST block. | BLK, ADV |
| FR-BLK-03 | Caveats MUST always be listed and MUST NOT affect defence. | BLK |
| FR-BLK-04 | Fail closed within a lane, isolated across lanes: any blocker in a lane (lane-wide or topic-scoped) MUST make that lane's earliest claim and its chains `not_defended`, and the report MUST keep the blocker's own scope (lane/topic/detector); a blocker in lane A MUST NOT affect lane B. | BLK, MUT |

### 1.4 Declared dependencies (FR-DEP)
| ID | Requirement | Verified by |
|---|---|---|
| FR-DEP-01 | Dependency file: schema 1, YAML or JSON, strict (unknown keys rejected at every level), `declared_by` required and non-empty, edge ids unique, no self-loops, endpoints are `/topic` strings or declared node ids, `relation` in the closed set, `window_ns` positive int (no bool, float, str), bindings exact-match and only for identity kinds the symptom carries. | DEP |
| FR-DEP-02 | Loader MUST return a problems list and never raise on bad input; any problem ⇒ exit 3, nothing analysed. | DEP, CLI |
| FR-DEP-03 | Limits: 1 MiB, 5,000 edges, 5,000 nodes, string length 256, no control characters, `safe_load` only, anchors/aliases/tags/merge keys refused. | DEP, ADV |
| FR-DEP-04 | Edges MUST NOT be inferred from the recording, topic names, message types or anything else. | DEP, ADV, MUT |
| FR-DEP-05 | Semantic hash MUST be independent of edge order, key order, YAML vs JSON syntax and whitespace; it MUST change when any edge, window, relation, binding or `declared_by` changes. | DEP, DET |
| FR-DEP-06 | Cycles are accepted; path enumeration is depth-bounded (32), count-bounded (10,000 per pair) and globally work-bounded (`path_work_limit`); a bound reached ⇒ `link_cap_reached`/`path_work_limit_reached` blocker with exact counts where exactly computable. | DEP, SCL |

### 1.5 Consistency, links, chains (FR-CON / FR-CHN)
| ID | Requirement | Verified by |
|---|---|---|
| FR-CON-01 | Edge label per DESIGN §4.4 truth table, same-lane only, exact boundary behaviour at `sep_min == window` (consistent) and `sep_min == window+1` (outside). | CON |
| FR-CON-02 | Multi-edge window = sum of edge windows iff every edge declares one; otherwise none. | CON |
| FR-CON-03 | Each `via_nodes` link MUST report per intermediate node whether symptoms lay between and which enabled detectors cover its topics; "not covered" MUST NOT read as "not affected". | CON, ADV |
| FR-CON-04 | Pairs with no declared path MUST NOT be linked or enumerated; only `no_declared_relation_count` is reported. | CON, MUT |
| FR-CHN-01 | Components = weakly connected sets under `consistent_with_propagation` links, singletons included, parameter-free. | CHN |
| FR-CHN-02 | Chains = maximal paths of the link DAG, computed iteratively (no recursion; 10,000-deep chain MUST work); the first symptom is the `head`, never "origin". | CHN, SCL |
| FR-CHN-07 | Bounded enumeration: only the deterministic first `chains_reported` chains are materialised; a global `path_work_limit` bounds all candidate-path work regardless of per-pair limits; `chains_total` is exact only when computable within `count_work_limit` (DP count), otherwise null with `bounded_unknown` and a lower bound; an estimate is never presented as a total; truncation/incomplete enumeration raises `chains_truncated` / `path_work_limit_reached` and downgrades affected claims. | CHN, ADV, SCL, MUT |
| FR-CHN-03 | Exactly the three hypothesis templates of DESIGN §4.5 MAY be emitted; no ranking, scoring, ordering by plausibility. Template 2 and 3 are always `not_excluded`. | CHN, WRD, MUT |
| FR-CHN-04 | `excluded_by_declared_edge_timing` is the only exclusion and MUST show both intervals and the declared edge. | CHN |
| FR-CHN-05 | A chain whose links were capped or whose component has a blocker MUST be `not_defended` with the blocker ids. | CHN, BLK |
| FR-CHN-06 | Localization (§4.6): locus = nodes of layer-0 symptoms of a component, frontier = nodes of its last layer, unlocalized = symptoms on nodes with no incident edge; none of these is worded as a fault location. | CHN, WRD |

### 1.6 Command and exit codes (FR-CLI)
| ID | Requirement | Verified by |
|---|---|---|
| FR-CLI-01 | `evidence localize PATH --anomaly-config FILE [--expectations FILE] [--dependencies FILE] [--detector ID]... [--json]`; read-only; writes nothing into PATH. | CLI |
| FR-CLI-02 | Exit codes per DESIGN §3.4 with precedence 3 > 2 > 1 > 0; `exit_reasons` lists all that applied. | CLI |
| FR-CLI-03 | Invalid input files MUST be detected before any analysis, even when evidence is also bad (exit 3 wins). | CLI |
| FR-CLI-04 | No `--fail-on-*` option exists. | CLI, PRES |

### 1.7 Report (FR-RPT)
| ID | Requirement | Verified by |
|---|---|---|
| FR-RPT-01 | Report top-level key set is exactly DESIGN §3.3; strict pydantic models, `extra=forbid`; absent optionals omitted. | RPT |
| FR-RPT-02 | No key or value named confidence, probability, score, rank, root_cause, likelihood, weight (any nesting). | RPT, WRD |
| FR-RPT-03 | Canonical JSON: sorted keys, integer times, and no float anywhere in the report (a float value is a test failure). | RPT, DET |
| FR-RPT-04 | Readable report and JSON are generated from the same model; every sentence in the readable report is built from a fixed template; data text is inert and truncated. | WRD, RPT |
| FR-RPT-05 | Every report states the known limitations listed in DESIGN §10 and the severity disclaimer. | RPT |

## 2. Provenance and uncertainty requirements (PR)
| ID | Requirement | Verified by |
|---|---|---|
| PR-01 | Every symptom, link, chain and hypothesis has ≥1 resolvable reference chain to an observation (artifact_ref, sha256, topic, message_index, log_time_ns); the model refuses to serialise one without. | PRV |
| PR-01b | Every blocker has a resolvable reference to its **actual source record** (inventory/coverage record, detector-result field or counter, M2 result, or M3d limit) with artifact digest(s) and scope; an observation reference is included only when one genuinely exists; an observation is never fabricated; a blocker with no source record is refused. | PRV, MUT |
| PR-02 | Independent replay verifier (test-only, written without reusing M3d logic) re-derives each referenced observation from the raw artifact and confirms topic, index, log time, and the onset-interval arithmetic. | PRV |
| PR-03 | Statement kinds are `observed` / `derived_order` / `declared` only; `declared` statements cite dependencies sha256, semantic hash, edge id, declared_by; a declared relation is never emitted as observed. | PRV, MUT |
| PR-04 | Identity text appears only as inert, truncated data; never inside a generated sentence. | WRD, ADV |
| PR-05 | Uncertainty is carried verbatim (source notes) plus the four M3d notes; no aggregation, no confidence field. | UNC, RPT |
| PR-06 | A tie, overlap, cap, blocker or unverifiable input only downgrades a claim. A metamorphic test MUST show that adding blockers never turns `not_defended` into `defended`. | UNC, ADV |

## 3. Non-functional requirements (NFR)
| ID | Requirement | Verified by |
|---|---|---|
| NFR-01 | Determinism: byte-identical report under `PYTHONHASHSEED` 0/1/random, different cwd, shuffled finding order, shuffled edge order, and tmp-dir name (artifact_ref permitting, same rule as M3c). | DET |
| NFR-02 | Integer nanoseconds only; no `float`, no wall clock, no randomness (AST/static test over the package). | DET, PRES |
| NFR-03 | No recursion on user-sized structures (static check + 10,000-deep test). | SCL |
| NFR-04 | Additive only: no accepted file modified except additive `cli.py` command; accepted pins hold; accepted suites pass unmodified (counts ≥ baseline) in both venvs. | PRES |
| NFR-05 | Import isolation: `localization` does not import `cdr`, `mcap`, `observations.load_evidence`, `anomaly.access`; `anomaly` and `investigator` do not import `localization`; no new mandatory dependency. | PRES |
| NFR-06 | Performance SHOULD (reported, measured, not assumed): 100k symptoms / 200k links localize in bounded time and memory measured by `tools/bench.py`; any budget miss is documented in the test report, never hidden. | SCL |
| NFR-07 | Read-only: file tree of PATH and of the inputs unchanged (hash before/after). | CLI, PRES |
| NFR-08 | Hostile-input safety: unbounded growth, billion-laughs, deep nesting and huge strings are refused with a problem, not a crash or hang. | ADV, DEP |
| NFR-09 | Test quality: mutation kill requirement per module (≥ 95 % of non-equivalent mutants killed; every survivor justified in writing). | MUT |

## 4. Traceability
Each requirement ID appears in at least one test ID in `M3D_TEST_PLAN.md` (column "Covers"); the final test report includes a requirement × test matrix generated by script and checked for empty rows.
