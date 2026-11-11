# M3d — symptom localization and failure-chain reconstruction

`physicore-collector evidence localize PATH --anomaly-config FILE [--expectations FILE] [--dependencies FILE] [--detector ID]... [--json]`

Built on the accepted M1/M2/M3c tree, which is unchanged (the only accepted file edited is `cli.py`: one added command). **Synthetic evidence only.** No real robot recording has been
used; real-recording validation and the decoder licensing review are outstanding.

## What the report says, and what it does not
It states **where symptoms were observed**, **in which order the recorder received them**, and **whether that order fits the dependencies you declared**. It never states a cause, a
probability, a score or a ranking. Recorder receipt time is not occurrence time; transport, QoS and recorder queueing are invisible. Severity labels are copied from the source findings
and are never used for ordering or grouping.

## Inputs
* **Evidence** `PATH`: anything `evidence inventory` accepts. Read-only; nothing is written.
* **`--anomaly-config`** (required): the M3c detection configuration. Only the detectors enabled there (and selected with `--detector`) can produce symptoms.
* **`--expectations`** (optional): an M2 contract; its divergences become symptoms.
* **`--dependencies`** (optional): your declared dependency graph. Without it the report is **order-only**: symptoms, clock lanes, layers and earliest symptoms; no links, chains or hypotheses.

### The dependency file (schema 1, YAML or JSON, strict)
```yaml
dependency_schema_version: 1
declared_by: "who asserts this graph"          # required
nodes: [{id: motor_driver}]                     # optional; topics are implicit nodes (ids must not start with "/")
bindings:                                       # optional; exact string matches only
  - {node: motor_driver, topic: /diagnostics, identity: {name: "motor_driver"}}
edges:                                          # required, non-empty
  - {id: e1, from: /cmd_vel, to: motor_driver, relation: command_response, window_ns: 500000000}   # window optional
```
Rules: unique edge ids; `from != to`; endpoints are `/topic` strings or declared node ids; `relation` is one of `data_flow`, `command_response`, `dependency` (a label only); `window_ns` a positive
integer; at most 5,000 nodes, 5,000 edges, 1 MiB, strings of at most 256 characters, no control characters, no YAML aliases. **Nothing is ever inferred** from the recording, topic names or message types.
The tool cannot verify your declarations; every link and chain says so. Cycles are allowed.

## How to read the report
* **Lanes.** Each clock domain (recorder log time; payload stamps per topic and field) is a lane. Lanes are never compared with each other.
* **Onset interval.** Each symptom has an interval, in integer nanoseconds, in which its onset lies. X is *before* Y only if X's interval ends strictly before Y's begins; touching or overlapping intervals are unordered.
* **Earliest symptom / layers.** Per lane, layer 0 holds the symptoms that nothing strictly precedes. If several tie, none is called the earliest. A defended earliest symptom means no listed blocker could have hidden an earlier one.
* **Link label** (one per pair of symptoms along a declared path):

| Label | Means | Does not mean |
|---|---|---|
| `consistent_with_propagation` | X is strictly before Y and any declared window is respected | X caused Y |
| `outside_declared_window` | X is before Y but further apart than the declared window | the edge is wrong |
| `inconsistent_order` | Y is strictly before X | that the declared edge is wrong; only its declared direction is contradicted |
| `undetermined` | the intervals overlap, so the order is unknown | no relation |
* **Chains.** Maximal paths of consistent links inside one lane (never across lanes), listed in a fixed order, at most 1,000. The exact total is given when it can be computed within a fixed bound, otherwise `bounded_unknown` with a lower bound, never an estimate.
* **Hypotheses.** Exactly three fixed templates, never ranked: `propagation_along_declared_edge` (per link), `common_or_independent_origin_not_excluded` and `observation_or_timing_artifact_not_excluded` (per chain, always `not_excluded`), with the evidence that would discriminate them.
* **Blockers.** Conditions that could have hidden an earlier symptom or limit a claim (incomplete recording, detector blind spot, truncation, a reached limit …). Each names its source record, the artifact digest and, only where one exists, a real observation reference. A blocked claim is downgraded to `not_defended`.
* **Localization** says where symptoms were observed (first and last layer nodes per component), not where a fault is. Nodes without a declared edge are listed as not localized.

## Exit codes
0 completed; 1 evidence partial or failed integrity (the report is still produced); 2 evidence not inspectable, or a usage error; 3 an input file is invalid (nothing is analysed). Precedence 3 > 2 > 1 > 0. There is no `--fail-on-*` option.

## Limits (recorded in every report; configurable only downward)
100,000 symptoms; 200,000 links (the exact number suppressed is reported); 1,000 reported chains; path depth 32; 10,000 paths per node pair; global path-work bound 1,000,000; chain-count bound 2,000,000. Measured resource use at these limits is in `docs/M3D_TEST_REPORT.md` (a 100,000-symptom worst case needs about 3.4 GB of memory).

## Worked example
`examples/localization/` holds a tiny synthetic recording, a configuration, a dependency file and the exact expected readable and JSON outputs (checked by a test). In it, the log message precedes the diagnostic error by 2 ms, inside the declared 10 ms window, so the link is labelled `consistent_with_propagation` — consistent with the declaration, nothing more.

## Limitations (every report states them)
1. Receipt order is not occurrence order. 2. A chain is a structure in the observed symptoms plus your declarations. 3. The detectors determine what can be a symptom. 4. Gap and rate symptoms cannot tell capture loss from silence; baselines come from the recording itself. 5. Validation is on synthetic recordings; real-recording validation and decoder licensing review are outstanding. 6. Mixed clocks give several earliest symptoms, which are not related. 7. The anomaly step's measured overhead exceeds its stated budget (unchanged).

## Verifying a report
`tests/localization/tools/replay_verifier.py` re-reads the raw recording and checks every evidence digest, evidence reference, log-time onset and blocker reference of a report without importing any M3d code.
