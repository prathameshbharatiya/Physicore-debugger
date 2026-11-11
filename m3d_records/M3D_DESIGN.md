# M3d — Symptom Localization and Failure-Chain Reconstruction: Technical Design (Revision 1 — APPROVED with clarifications A and B)

Status: **APPROVED (design and test plan). Revision 1 = Revision 0 + clarifications A (blocker provenance, P1) and B (bounded chain enumeration, §4.5/§6). Decisions D1–D11 are approved as recommended (§9). Implementation proceeds under `M3D_IMPLEMENTATION_PLAN.md`; Step 11 delivery needs separate authorization.**
Baseline: the delivered M3c tree (commit `b7bf26044b49…`, tree `f3f51e7cc7a4…`; deliverable hashes in `M3C_DELIVERABLES.sha256`). M3c, M3b, M3a, Phase 2 and Phase 1 deliverables are not reopened, and their pinned hashes stay the acceptance baseline.
Naming used here: **M1** = the Phase 1 collector (capture, incident, export packages); **M2** = Phase 2 (evidence inventory and the expectation-based investigator); M3a/M3b/M3c as delivered. (Assumption — please correct if M1/M2 mean something else.)
Origin: `DEBUGGER_MILESTONE_PROPOSAL.md` §5 ("failure localization without overclaiming"). This design makes that section precise.

---------------------------------------------------------------------

## 0. Position, in one paragraph

A recording shows **what was received, and when it was received**. It cannot show why. M3d therefore does not find a root cause. It (1) turns the symptoms that M3c and M2 already established into
uniform, evidence-anchored *symptom records* with an honest statement of *when they began* (an interval, not a point); (2) states, per clock domain, which symptom is the **earliest observable one** and
whether that statement is **defended** or blocked, and by exactly what; (3) states **temporal precedence** (`received_before`) only where the intervals force it; (4) where a person has **declared**
dependency edges between topics, labels each symptom pair `consistent_with_propagation`, `inconsistent_order`, `outside_declared_window` or `undetermined`; and (5) assembles the consistent links into
**failure-chain candidates** — ordered, evidence-referenced sequences of symptoms along declared edges — each shipped with the alternative explanations that were **not** excluded. A "chain" is a structure
in the observed data plus the user's declarations. It is never a claim about physical cause.

## 1. Scope

### 1.1 In scope
* Symptom normalisation from M3c findings and (optionally) M2 investigation divergences.
* Onset-interval semantics per symptom category; strict interval order; temporal layers.
* Earliest observable symptom per clock domain, with the defence analysis and a complete blocker list.
* A human-supplied dependency file (nodes = topics, optional bindings of identities to nodes, directed edges with an optional latency window).
* Edge consistency labels, link graph, connected components ("incidents"), failure-chain candidates, the fixed set of three hypothesis templates.
* A read-only command `evidence localize` with a JSON report and a readable report; exit codes in the M3c style.

### 1.2 Explicitly out of scope (by decision, not by omission)
Root-cause statements; confidence scores or probabilities; ranking of hypotheses; learned or tuned weights; LLM involvement; cross-clock alignment, offset estimation or attestation; multi-recording comparison;
TF-tree or geometry; new detectors or changes to M3c/M3b/M3a semantics; incident memory/retrieval; rendering a final human report (that is M3e, which will consume this report); a streaming loader; real-recording
validation (still outstanding and still not claimed); decoder licensing clearance (still outstanding).

### 1.3 Standing constraints carried forward
Additive only; no accepted file changes except one additive `cli.py` command (§8.2, needs approval); no new mandatory dependency; `cdr.py` stays the only importer of the third-party decoder; Phase 2/M3a/M3b/M3c
output stays byte-identical; nothing is pushed; integer nanoseconds everywhere; no wall clock; no randomness.

## 2. Architecture

### 2.1 Principles
1. **Derived, never re-observed.** M3d never opens an artifact and never decodes a payload. Its only inputs are the typed outputs of M3c and M2 (computed in-process from the same inventory) plus the user's files. Every statement it makes points back to a finding and, through it, to artifact/sha256/topic/message_index.
2. **Three kinds of statement, never mixed:** `observed` (comes from a finding), `derived_order` (a deterministic comparison of two observed intervals, with the rule id and both intervals shown), `declared` (comes from the dependency file, with the file digest, edge id and `declared_by`). A statement of any other kind is rejected by the report model.
3. **Intervals, not points.** Onset is an interval `[lower_ns, upper_ns]` in one clock domain. "X before Y" holds only if `X.upper < Y.lower`. Anything else is `unordered`. Equal instants are unordered.
4. **Domains are typed and never converted.** Comparison raises across domains (reusing the M3c clock type). Lanes never merge.
5. **Absence of a symptom is not evidence of health.** A detector that was not configured, not applicable, or could not evaluate says nothing about its topics; M3d reports that as scope, not as "nothing happened".
6. **Fail closed on defence, open on information.** Anything that could hide an earlier symptom blocks the "defended" label (it never blocks reporting). The blocker list is exact.
7. **No scores, no ranking, no defaults for judgement.** Every numeric judgement threshold (an edge window) is declared by the user or absent; absent means "order only".
8. **Determinism.** Same inputs => byte-identical canonical JSON, independent of finding order, edge order, hash seed, or working directory.

### 2.2 Data flow
```
evidence path ──► inventory ──► (M3c run: findings, results) ─┐
                         └────► (M2 investigate, optional) ───┼─► symptoms ─► lanes ─► intervals/layers ─► earliest + blockers
anomaly config ───────────────────────────────────────────────┘                                         │
dependency file ─► validated graph ─────────────────────────────────────────────► edge consistency ─► links ─► components ─► chains ─► hypotheses
                                                                                                          └──► report (JSON / readable)
```
All stages after "symptoms" are pure functions of (symptoms, blockers, graph, parameters).

### 2.3 Module boundaries (new package `physicore_collector.localization`)
| Module | Responsibility | May import |
|---|---|---|
| `intervals.py` | `Interval(domain, lower_ns, upper_ns)`; `precedes`, `overlaps`, `min_separation`, `max_separation`; raises on domain mismatch; integer-only | `anomaly.clock` |
| `symptoms.py` | `Symptom` model; normalisation tables (§4.1); M3c-finding and M2-finding adapters | `anomaly.findings`, `investigator` public models |
| `dependencies.py` | load/validate the dependency file (problems list, never raises); graph build; ancestors; bounded path enumeration; semantic hash | `yaml`, stdlib |
| `blockers.py` | derive blockers and caveats from detector results, coverage, truncation, M2 results | `anomaly.findings` |
| `precedence.py` | strict order, layers, earliest set, defence decision | `intervals`, `blockers` |
| `consistency.py` | per-edge pair judgement | `intervals`, `dependencies` |
| `chains.py` | link graph, components, chain extraction, hypothesis templates | all of the above |
| `report.py` | strict report models, canonical JSON, readable renderer, controlled vocabulary, wording check | `anomaly.findings` (forbidden-term list) |
| `service.py` | orchestration: validate inputs (exit 3 first), inspect, run M3c (and M2), build, decide exit code | `anomaly.service`, `inventory`, `investigator` |

Rules: `localization` may import `anomaly` and `investigator` public APIs; **neither may import `localization`** (tested). It never imports `observations.load_evidence`, `cdr`, `mcap`, or `access` (it must not read evidence itself). `cli.py` imports it lazily inside the command.

## 3. Inputs and contracts

### 3.1 Command
`physicore-collector evidence localize PATH --anomaly-config FILE [--expectations FILE] [--dependencies FILE] [--detector ID]... [--json]`
* `--anomaly-config` is mandatory (the M3c configuration; M3d adds no detector configuration of its own).
* `--expectations` optional: an M2/M3b contract; its divergences join the symptom set.
* `--dependencies` optional: without it M3d still reports symptoms, lanes, layers, earliest and blockers, but no consistency labels, links, chains or hypotheses, and says so.
* No `--fail-on-...` flag: there is no verdict to fail on.

### 3.2 Dependency file (schema 1; YAML or JSON; strict, unknown keys rejected at every level)
```yaml
dependency_schema_version: 1
declared_by: "name or team that asserts this graph"       # required, non-empty, inert text
nodes:                                                     # optional; topics are implicit nodes
  - id: motor_driver                                       # a named node that groups bindings
bindings:                                                  # optional, exact matches only
  - {node: motor_driver, topic: /diagnostics, identity: {name: "motor_driver"}}   # a diagnostic status name
  - {node: nav,          topic: /rosout,      identity: {name: "nav"}}            # a logger name
edges:                                                     # required, non-empty
  - {id: e1, from: /cmd_vel, to: /odom, relation: command_response, window_ns: 500000000}   # window optional
  - {id: e2, from: /odom,    to: motor_driver, relation: data_flow}
```
Rules (every violation => invalid dependency file => exit 3, problems listed, nothing analysed): `declared_by` required; edge ids unique; `from != to`; every endpoint is a topic string starting with `/` or a declared node id;
`relation` in `{data_flow, command_response, dependency}` (a label only; it changes no logic); `window_ns` a positive integer if present, never a float or bool; bindings use exact string equality on the identity value
and only for identity kinds the symptom carries (logger name, diagnostic name/hardware_id, goal id, battery serial/location); limits (§6): at most 5000 edges, 5000 nodes, file ≤ 1 MiB, YAML parsed with `safe_load`, alias/anchor expansion refused, string length ≤ 256, control characters refused.
Cycles are allowed (feedback loops exist); chain extraction is acyclic because every link is strictly forward in time.
There is no inference of edges from the recording, from topic names, or from message types. Ever.

### 3.3 Output: `LocalizationReport` (schema 1.0; canonical JSON, sorted keys, integers only for times)
Top-level keys (exact set, tested): `localization_schema_version`, `tool_version`, `evidence[]` (artifact_ref + sha256), `inputs` (`anomaly_config_hash`, `expectations_sha256|null`, `dependencies{sha256, semantic_hash, edge_count}|null`),
`parameters_hash`, `limits`, `lanes[]`, `symptoms[]`, `links[]`, `components[]`, `chains[]`, `chains_total`, `chains_total_status`, `hypotheses[]`, `blockers[]`, `caveats[]`, `localization`, `coverage`, `limitations[]`, `severity_disclaimer`, `exit_code`, `exit_reasons[]`, `problems[]|absent`.
No `confidence`, no `root_cause`, no score of any kind (tested).

Key records (all strict pydantic, `extra=forbid`):
* **Symptom** — `symptom_id` (hash of source id + source kind + onset interval + parameters), `source` (`m3c_finding` | `m2_finding`) and `source_id`, `detector_or_rule`, `category`, `topic`, `node` (topic or bound node), `identity` (inert, truncated text, or null), `severity` and `severity_basis` copied from the source and **never used for ordering or aggregation**, `extent` (first/last observed, source domain), `onset` (Interval in the lane domain), `onset_basis` (code, §4.1), `lanes` (domain list), `evidence` (the source's observation refs verbatim: artifact_ref, sha256, topic, message_index, log_time_ns), `source_coverage`, `source_uncertainty` (codes carried unchanged), `truncated` flag.
* **Lane** — domain, symptom count, `layers[]` (ordered groups of mutually unordered symptom ids), `earliest` (`symptom_ids` of minimal elements, `defence`: `defended` | `not_defended` | `tied` | `none_observed`, `blocker_ids[]`).
* **Link** — `from_symptom`, `to_symptom`, `edge_path` (edge ids), `path_kind` (`direct` | `via_nodes`), `label` (`consistent_with_propagation` | `inconsistent_order` | `outside_declared_window` | `undetermined`), `min_separation_ns`, `max_separation_ns`, `window_ns|null`, `via_nodes[]` each with `symptom_ids_between` and `coverage` (which enabled detectors cover that node's topics), `statement_kind: derived_order` with the rule id and both intervals, and the `declared` references (dependencies sha256, edge ids, `declared_by`).
* **Chain** — `chain_id`, ordered `symptom_ids`, `link_ids`, `head` (the first symptom: "earliest in this chain", not "origin"), `defence` (`defended_structure` | `not_defended` with blocker ids), `hypotheses[]`, `unobserved_intermediate_nodes[]`, `component_id`.
* **Component** — weakly connected set of symptoms under `consistent_with_propagation` links (singletons included), the "incident" grouping; parameter-free.
* **Hypothesis** (fixed templates, §5.5) — `template`, `status`, `supporting[]`, `contradicting[]`, `discriminating_evidence_needed[]` (plain text from a fixed vocabulary), all as statement references. No score.
* **Blocker / Caveat** — code, scope (detector, topic, lane), exact count, detail (inert text), and the source result/field it came from.

### 3.4 Exit codes
| Exit | Meaning |
|---:|---|
| 0 | completed (including "no symptoms", "no dependency file", blocked defences) |
| 1 | evidence partial / integrity failed or unverifiable (accepted `inventory.exit_code == 1`); the report is produced |
| 2 | evidence not inspectable (a report states `evidence_not_inspectable`), or a usage error |
| 3 | an input file is invalid: the anomaly configuration, the expectations contract, or the dependency file; nothing is analysed |
`exit_reasons` in the report lists which applied. Precedence: 3, then 2, then 1, then 0.

## 4. Methodology

### 4.1 Symptom normalisation and onset semantics
A finding's own times are not enough: a gap *began* somewhere between its two bounding messages. Each source category gets a fixed **onset rule**; the rule id is carried in `onset_basis`. All times are in the lane domain
(`mcap_log_time` for the primary lane; receipt of the observation, never an interpretation of payload clocks).

| Source (M3c category / M2 kind) | `onset_basis` | Onset interval | Notes |
|---|---|---|---|
| `log_severity`, `diagnostic_status`, `goal_status`, `power_status` | `received_message` | `[t_first, t_first]` (point); `extent` = `[t_first, t_last]` for a grouped run | grouped findings keep the whole run as extent |
| `non_finite_value`, `array_length_change` | `received_message` | point at first message of the run | run extent kept |
| `timestamp_regression`, `timestamp_duplicate` | `received_message` | receipt time of the offending message (log lane); the payload stamp is carried as an annotation, never ordered against log time | optional secondary lane `payload_stamp:<topic>:<field>` holds the stamp value, ordered only against other symptoms of that same domain |
| `publishing_gap` | `silence_between_captured_messages` | `[t_before, t_after]` | the true start of silence is unknown inside the interval; capture loss vs silence is indistinguishable (note carried) |
| `rate_change` | `window_span` | `[window_start, window_end]` | a window-level symptom; extent equals onset |
| M2 `transition`, range/tolerance/equals | `received_sample` | point at the divergent sample | uses M2's own time domain; non-log domains go to their own lane |
| M2 `event_response` deadline expiry | `deadline_window` | `[trigger_time, expiry_time]` | "expiry" is where M2 places the divergence; the response may still have arrived late (carried) |

The table is **verified in step 2 against the fields each detector actually emits**; any mismatch is reported, not adapted silently (the Step 0 practice of M3c).
Normalisation copies, never recomputes: severity, notes, coverage, identity and evidence references come from the source unchanged. A source finding with `observations_truncated` yields a symptom with `truncated=true` and a blocker.

### 4.2 Interval order, layers, earliest observable symptom
* **Strict precedence** `X ≺ Y` (same lane): `X.onset.upper < Y.onset.lower`. This is an interval order (irreflexive, asymmetric, transitive); property-tested.
* **Layers**: layer(X) = length of the longest `≺`-chain ending at X, minus one. Symptoms in one layer are mutually unordered *or* ordered only through non-adjacent ones; a layer is reported as an ordered list of sets. Computed in O(n log n) by a sort + sweep.
* **Earliest set** of a lane = the minimal elements (layer 0). One element `m` is a *candidate earliest*; the claim is **defended** iff **all** hold: (a) layer 0 contains exactly one symptom (no tie, no overlap with another); (b) there is no **blocker** in the lane (§4.3); (c) `m` is not `truncated`. A tie is reported as `tied` with all members. A defended earliest says only: *"this is the earliest symptom observed by the detectors that ran, under their parameters, with receipt-time semantics."*
* "Earliest" is per lane. With several domains there are several earliest symptoms, and the report says they are unordered across domains.

### 4.3 Blockers and caveats
A **blocker** is anything that could have hidden an earlier symptom in a lane. A **caveat** is a standing limitation that is always listed but never blocks.
| Class | Code | Source |
|---|---|---|
| blocker | `recording_incomplete` | evidence completeness / M3c `coverage.topic_coverage == incomplete` |
| blocker | `detector_blind_spot`, `detector_insufficient_evidence` | M3c result status of an **enabled** detector with configured scope |
| blocker | `undecodable_messages`, `unparseable_messages` | M3c result counters |
| blocker | `findings_truncated` | M3c `findings_truncated` (exact suppressed count) |
| blocker | `observations_truncated` | a symptom's source says so |
| blocker | `m2_expectation_not_evaluated`, `m2_blind_spot_before_or_unplaced` | M2 results (same rule as M2's own "defended" test) |
| blocker | `symptom_cap_reached`, `link_cap_reached`, `chains_truncated`, `path_work_limit_reached` | M3d limits (§4.5, §6) |
| caveat | `boundary_gaps_not_evaluated` | M3c gap result |
| caveat | `receipt_order_not_occurrence_order` | always |
| caveat | `capture_loss_indistinguishable_from_silence` | present gap symptoms |
| caveat | `baseline_from_recording` / `baseline_contaminated_possible` | carried from symptoms |
| caveat | `detector_not_configured` / `detector_not_applicable` | scope statement, never "nothing happened" |
| caveat | `real_recording_validation_outstanding` | always |

### 4.4 Declared-dependency consistency
Defined only for a declared path from node U to node V and symptoms X on U and Y on V in the **same lane**. With `sep_min = Y.lower − X.upper` and `sep_max = Y.upper − X.lower`:
| Condition | Label |
|---|---|
| `X ≺ Y` and (no window, or `sep_min ≤ window`) | `consistent_with_propagation` |
| `X ≺ Y` and `window` declared and `sep_min > window` | `outside_declared_window` |
| `Y ≺ X` | `inconsistent_order` (downstream strictly before upstream; this says only that propagation along *this* edge does not fit the order) |
| otherwise (overlap) | `undetermined` |
For a multi-edge path the window is the sum of the edge windows if **every** edge declares one, else none. A path longer than one edge is a `via_nodes` link; each intermediate node reports whether it had symptoms between X and Y and which enabled detectors cover its topics
(no symptom at a node no detector looked at is "not covered", never "not affected"). Pairs with no declared path are **not** enumerated; only counts are reported (`no_declared_relation_count`).
Link enumeration is bounded (§6) and, if capped, the exact suppressed count is reported and chains are marked `not_defended`.

### 4.5 Components, chains, hypotheses
* **Components**: weakly connected components of the `consistent_with_propagation` link graph (symptoms as vertices). Parameter-free.
* **Chains**: maximal directed paths in the link DAG (iterative, no recursion; the DAG property follows from strict time order). A chain's **head** is its first symptom. The report never says "origin", "trigger" or "culprit".
  **Bounded enumeration (clarification B).** Chains are *not* all materialised to be counted. (1) Maximal paths are enumerated lazily in a deterministic order (heads by canonical symptom order; successors by canonical link order), and only the first `chains_reported` (default 1,000) are kept. (2) A **global candidate-path work budget** (`path_work_limit`, default 1,000,000 node-visits across the whole run; the per-pair limit of 10,000 paths can never multiply beyond it) bounds all path processing. (3) The total number of maximal chains is computed by dynamic programming over the link DAG (count of maximal paths per vertex, big integers, O(V+E)) **only when** V+E ≤ `count_work_limit` (default 2,000,000); then `chains_total` is exact and `chains_total_status = exact`. Otherwise `chains_total` is null with `chains_total_status = bounded_unknown` and a `lower_bound` (number actually enumerated) is given; an estimate is never presented as a total. (4) If enumeration stopped because of the report limit or the work budget, the blocker `chains_truncated` (with exact reported count and, when known, exact suppressed count) or `path_work_limit_reached` is raised and every affected chain/component claim is `not_defended`.
* **Hypothesis templates** (fixed; no others may be emitted):
  1. `propagation_along_declared_edge` (per link): status `consistent_with` (the link label) or `excluded_by_declared_edge_timing` — the *only* exclusion M3d can state, and only relative to the declared edge's own direction and window, with both intervals shown.
  2. `common_or_independent_origin_not_excluded`: always `not_excluded`. Facts listed: declared common ancestors of the chain's nodes; whether each ancestor had symptoms before the chain head; which detectors covered the ancestor.
  3. `observation_or_timing_artifact_not_excluded`: always `not_excluded`. Facts: receipt-order caveat, recording completeness, gap/loss ambiguity, baseline notes.
  `discriminating_evidence_needed` comes from a fixed vocabulary (for example: "an intervention or a repeat recording", "recorder-side drop counters (not available in this format)", "a declared edge for the ancestor", "detector coverage of the ancestor's topics").
* No hypothesis is ranked, scored or called "most likely".

### 4.6 Localization (the "where")
Without a dependency file: symptoms are localized to **topics** and time layers only. With one: (1) the **locus** of a component = the nodes of its layer-0 symptoms; (2) the **frontier** = nodes of its last-layer symptoms; (3) **unlocalized** symptoms = those whose node has no incident declared edge (listed with their topic; never forced into a component). Localization states *where symptoms were observed*, not where a fault is.

## 5. Evidence provenance and uncertainty rules
**P1** Every symptom, link, chain and hypothesis carries ≥1 resolvable reference chain: `statement → finding id → observation (artifact_ref, sha256, topic, message_index, log_time_ns)`. Every **blocker** carries a resolvable reference to its **actual source record** (an inventory/coverage record, a detector-result field, a counter, an M2 result, or an M3d limit), with the artifact digest(s) and applicable scope; it carries an observation reference **only when one genuinely exists** (for example an observation-backed `observations_truncated`). An observation is never fabricated to satisfy provenance. The model refuses to serialise a statement without its required reference and refuses a blocker with no source record.
**P2** M3d adds no raw payload values. Identity labels (logger names, diagnostic names) appear only as inert, truncated text and are never interpolated into generated sentences.
**P3** `declared` statements cite `dependencies.sha256`, `semantic_hash`, edge id and `declared_by`. M3d never presents a declared relation as observed.
**P4** Evidence identity is bound by construction: M3c and M2 run in-process on the same inventory; the report lists the same artifact digests. Importing precomputed reports is **not** supported in v1 (it would need digest re-verification and replay).
**P5** Uncertainty is carried, not summarised: source notes are copied verbatim; M3d adds `onset_interval_width`, `declared_edge_unverified`, `path_via_unobserved_nodes`, `blocked_defence`. There is no confidence field and no aggregation of severities.
**P6** Controlled vocabulary: every label, status, hypothesis template, blocker code and discriminating-evidence phrase comes from closed enumerations. Generated sentences are built from fixed templates; the forbidden-term check (M3c's list plus: `root cause`, `origin`, `originated`, `triggered`, `culprit`, `responsible`, `blame`, `fault in`, `explains`, `therefore`) runs on every generated string. The label token `consistent_with_propagation` is the only place the word "propagation" may appear.
**P7** Receipt time is the only cross-topic clock; payload clocks stay in their own lanes; no conversion ever.
**P8** Decisions that could mislead fail closed: a tie, an overlap, a cap, a blocker, an unverifiable import all downgrade; none upgrades.

## 6. Performance and limits (configurable only downward, recorded in the report)
Default hard limits (approved, D8): symptoms 100,000; links 200,000; chains reported 1,000; dependency file 1 MiB / 5,000 edges / 5,000 nodes / 256-char strings; path depth 32 and 10,000 paths per (U,V) pair; plus the **global** bounds introduced by clarification B: `path_work_limit` 1,000,000 node-visits and `count_work_limit` 2,000,000 (V+E). Any limit reached is a blocker with exact counts where exactly computable; the chain total is exact only when safely computable (§4.5), otherwise `bounded_unknown`. Complexity targets: symptom normalisation O(n); layers O(n log n); links O(links) via per-node sorted lists and two-pointer scans; chains iterative O(V+E). Memory is dominated by the findings M3c already produced.

## 7. Failure modes and how each is surfaced
| Situation | Behaviour |
|---|---|
| No symptoms | `none_observed` lanes; blockers still listed; exit 0 |
| Dependency file absent | order-only report; states "no dependencies declared" |
| Symptom on a node with no edges | in layers and earliest; unlocalized in §4.6; no links |
| Edge declared but one endpoint has no symptoms | no links; edge listed as `no_symptom_on_endpoint` in coverage |
| Overlapping/tied onsets | `unordered` / `undetermined`; never an arbitrary order |
| M3c detector blind or insufficient | blocker for the lane, exact scope |
| Payload-stamp symptom | log lane by receipt, plus own-domain lane; the two lanes are never compared |
| Findings truncated upstream | blocker `findings_truncated` |
| Hostile text in logger names | inert truncated label; wording check untouched by data |
| Cap reached | exact counts; `not_defended` |

## 8. Dependencies and edits to accepted files
### 8.1 New files
`src/physicore_collector/localization/*.py` (§2.3), `examples/localization/*`, `docs/M3D_LOCALIZATION.md`, tests under `tests/localization/`.
### 8.2 Edits to existing files (each needs approval)
| File | Edit | Risk |
|---|---|---|
| `cli.py` | one additive command `evidence localize` on `evidence_app`, lazy import; existing commands' `--help` byte-identical (golden-tested against the M3c tree); additive-only diff (no deleted line) | low; this is the same kind of edit M3c made |
| `anomaly/*`, `investigator/*` | **none.** If a public accessor is missing, it is reported as a blocker and a decision requested; nothing is patched in place | — |
| `pyproject.toml` | none (PyYAML is already a core dependency) | — |

## 9. Decisions requested (recommended answers in bold)
* **Approved 2026-11-03 (recorded verbatim in `M3D_DECISIONS.md`).** D1–D11 below approved as recommended; terminology confirmed (M1 = Phase 1 Evidence Collector; M2 = Phase 2 inventory and expectation-based investigator).
* **D1 command name:** `evidence localize` (alternative: `evidence reconstruct-chain`).
* **D2 `cli.py` additive edit** to M3c's tree for the new command: **approve**.
* **D3 input mode:** in-process computation only in v1; no import of precomputed M3c/M2 reports: **approve**.
* **D4 `window_ns` optional per edge;** absent means order-only judgement: **approve**.
* **D5 bindings** (identity → node) exact-match only, declared in the dependency file; no fuzzy matching: **approve**.
* **D6 onset-interval semantics** (§4.1) and strict interval order, equal instants unordered: **approve**.
* **D7 include M2 divergences** as symptoms when `--expectations` is given: **approve**.
* **D8 limits** (§6): **approve** as defaults.
* **D9 no `--fail-on` flag** and the exit-code table (§3.4): **approve**.
* **D10 the three fixed hypothesis templates and closed vocabulary;** no scoring or ranking: **approve**.
* **D11 secondary payload-stamp lanes** are produced only for `stamp_order` symptoms: **approve** (alternative: log lane only in v1, which is simpler).

## 10. Known limitations (will be stated in every report)
1. Receipt order is not occurrence order; transport, QoS and recorder queueing are invisible.
2. A chain is a structure in the observed symptoms plus the user's declarations; the declarations are unverifiable by the tool.
3. The detectors determine what can be a symptom; a fault that no enabled detector reports does not exist here.
4. Gap and rate symptoms cannot tell capture loss from silence; mode baselines come from the recording itself.
5. Synthetic validation only; real-recording validation and the decoder licensing review remain outstanding.
6. Mixed or unrelated clocks give several earliest symptoms; the tool will not relate them.
7. Memory budget and performance claims of M3c are unchanged (M3c's measured overhead exceeds its stated budget; see its report).
