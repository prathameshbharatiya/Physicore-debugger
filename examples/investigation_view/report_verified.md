# Investigation report

View of a localization report (schema 1.0, tool version 0.1.0). This view adds no analysis: every statement below is in the report.

Scope: This is the earliest symptom observed by the detectors that ran, under their configured parameters, using recorder receipt time. It is not a statement about which event occurred first in the robot.

Severity: Severity labels are copied from the source findings. They are not used for ordering, grouping or aggregation and carry no statement of impact.

Order is not a statement about what produced a symptom: an arrow or a label describes the receipt order of recorded messages and the declared dependency, nothing more.

## Summary

Exit code 0: completed.

Counts: 2 symptom(s), 1 link(s) reported of 1, 1 component(s), 1 chain(s) reported, 0 blocker(s), 9 caveat(s), 2 evidence reference(s).

At most 200 rows are listed per table and at most 20 items per cell; every omitted row or item is counted where it is cut and is present in the JSON report.

## First divergence

No symptom from an expectation contract is present in this report.

## Lanes and earliest symptoms

### Lane 1 of 1

Clock domain: mcap_log_time. Symptoms: 2. Layers: 2. Axis start: 1700000000001000000 ns.

This lane has its own time axis; it is never compared with another lane.

Statement for this lane: This is the earliest symptom observed by the detectors that ran, under their configured parameters, using recorder receipt time. It is not a statement about which event occurred first in the robot.

Earliest symptom(s) in this lane: s-c872a01752e24777.

| Symptom | Layer | Interval | Offset from lane start | Basis | Topic | Severity as reported |
|---|---|---|---|---|---|---|
| s-c872a01752e24777 | 0 | 1700000000001000000 ns | +0.000000 ms | received_message | /rosout | error |
| s-4bd2015939fdeda6 | 1 | 1700000000003000000 ns | +2.000000 ms | received_message | /diagnostics | error |

## Symptoms

| Symptom | Source | Detector or rule | Category | Topic | Node | Onset interval | Basis | Severity as reported | Source coverage | Truncated | Identities | Notes | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| s-4bd2015939fdeda6 | m3c_finding | tierA.diagnostics | diagnostic_status | /diagnostics | /diagnostics | 1700000000003000000 ns | received_message | error | complete | no | diagnostic_name=motor_driver; hardware_id=hw1 | constants_from_builtin_table | E2 |
| s-c872a01752e24777 | m3c_finding | tierA.ros_log | log_severity | /rosout | /rosout | 1700000000001000000 ns | received_message | error | complete | no | logger_name=navigation | constants_from_builtin_table | E1 |

## Links

What each link label means (the label is about receipt order and the declared window only):

- consistent_with_propagation: the recorder received the symptom on the upstream node strictly before the symptom on the downstream node, within the declared window.
- inconsistent_order: the symptom on the downstream node was received strictly before the symptom on the upstream node.
- outside_declared_window: the receipt order follows the declared direction, but the separation is outside the declared window.
- undetermined: the recorded intervals do not allow the order or the window check to be decided (they touch or overlap).

Notes on links: declared_edge_unverified means the dependency was declared by a person and cannot be verified by this tool; path_via_unobserved_nodes means the declared path passes through nodes that had no symptom between the two.

| Link | Lane | From | To | Edges | Label | Separation | Window | Notes |
|---|---|---|---|---|---|---|---|---|
| l-3e86390a65b78bbe | mcap_log_time | s-c872a01752e24777 on /rosout | s-4bd2015939fdeda6 on /diagnostics | e1 | consistent_with_propagation | 2000000 ns to 2000000 ns | 10000000 ns | declared_edge_unverified |

## Chains and components

Chains: 1 in total (exact).

| Chain | Lane | Head | Symptoms in order | Nodes | Defence | Blockers |
|---|---|---|---|---|---|---|
| ch-a504204354b92e69 | mcap_log_time | s-c872a01752e24777 | s-c872a01752e24777 then s-4bd2015939fdeda6 | /rosout then /diagnostics | defended_structure | none |

| Component | Lanes | Symptoms | Links | Defence | Blockers |
|---|---|---|---|---|---|
| c-8c302e6e642646ee | mcap_log_time | s-4bd2015939fdeda6, s-c872a01752e24777 | 1 | defended_structure | none |

Localization states where symptoms were observed; it does not state where a fault is.

| Component | Lane | First-layer nodes | Last-layer nodes |
|---|---|---|---|
| c-8c302e6e642646ee | mcap_log_time | /rosout | /diagnostics |

| Lane | Topic | Layer | Symptoms |
|---|---|---|---|
| mcap_log_time | /diagnostics | 1 | s-4bd2015939fdeda6 |
| mcap_log_time | /rosout | 0 | s-c872a01752e24777 |

Pairs of symptoms with no declared path in either direction (counted per lane): 0. Pairs with a declared path but no common lane (not compared): 0.

## Hypotheses

The statements below are not ranked and carry no score or probability. Each says what the declared dependencies and the observations do or do not exclude, and what further evidence would discriminate.

| Link | Template | Status | Statement |
|---|---|---|---|
| l-3e86390a65b78bbe | propagation_along_declared_edge | consistent_with | The order of these two symptoms is consistent with the declared edge. |

| Chain | Template | Status | Statement | Declared ancestors not on the chain | Evidence that would discriminate |
|---|---|---|---|---|---|
| ch-a504204354b92e69 | common_or_independent_origin_not_excluded | not_excluded | A common or independent source of these symptoms is not excluded by the declared dependencies or the observations. | 0 (0 listed): none | an intervention or a repeat recording; a declared edge for the ancestor |
| ch-a504204354b92e69 | observation_or_timing_artifact_not_excluded | not_excluded | An artifact of observation or timing is not excluded: recorder receipt time is not occurrence time, and the recording may be incomplete. | none | an intervention or a repeat recording; recorder-side drop counters (not available in this format) |

## Blockers and caveats

None.

| Code | Detail | Detector |
|---|---|---|
| detector_not_configured | A detector was not configured; nothing is said about what it would have seen. | tierA.battery |
| detector_not_configured | A detector was not configured; nothing is said about what it would have seen. | tierA.goal_status |
| detector_not_configured | A detector was not configured; nothing is said about what it would have seen. | tierB.array_length |
| detector_not_configured | A detector was not configured; nothing is said about what it would have seen. | tierB.non_finite |
| detector_not_configured | A detector was not configured; nothing is said about what it would have seen. | tierB.publishing_gap |
| detector_not_configured | A detector was not configured; nothing is said about what it would have seen. | tierB.rate_change |
| detector_not_configured | A detector was not configured; nothing is said about what it would have seen. | tierB.stamp_order |
| real_recording_validation_outstanding | Validation against real robot recordings is outstanding. | any |
| receipt_order_not_occurrence_order | Recorder log time is receipt order, not occurrence order. | any |

## Coverage

| Detector | Status |
|---|---|
| tierA.battery | not_configured |
| tierA.diagnostics | findings_reported |
| tierA.goal_status | not_configured |
| tierA.ros_log | findings_reported |
| tierB.array_length | not_configured |
| tierB.non_finite | not_configured |
| tierB.publishing_gap | not_configured |
| tierB.rate_change | not_configured |
| tierB.stamp_order | not_configured |

Links reported: 1 of 1; suppressed: 0; status: complete.

Declared path pairs related: 1; examined: 1; not examined: 0.

| Edge | From | To | Symptoms on first | Symptoms on second | Status |
|---|---|---|---|---|---|
| e1 | /rosout | /diagnostics | 1 | 1 | endpoints_have_symptoms |

## Limits and limitations

Limit chains_reported: 1000.

Limit count_work_limit: 2000000.

Limit dependency_edges: 5000.

Limit dependency_file_bytes: 1048576.

Limit dependency_nodes: 5000.

Limit dependency_string_chars: 256.

Limit links: 200000.

Limit path_depth: 32.

Limit path_work_limit: 1000000.

Limit paths_per_pair: 10000.

Limit symptoms: 100000.

Limitation 1: Receipt order is not occurrence order; transport, QoS and recorder queueing are invisible.

Limitation 2: A chain is a structure in the observed symptoms plus the user's declarations; the declarations cannot be verified by this tool.

Limitation 3: The detectors determine what can be a symptom; a fault that no enabled detector reports does not appear here.

Limitation 4: Gap and rate symptoms cannot tell capture loss from silence; mode baselines come from the recording itself.

Limitation 5: Validation so far is on synthetic recordings; validation on real robot recordings and the decoder licensing review are outstanding.

Limitation 6: Mixed or unrelated clocks give several earliest symptoms; this tool does not relate them.

Limitation 7: The memory and performance statements of the anomaly detection step are unchanged; its measured overhead exceeds its stated budget.

## Evidence references

Verification against the recording at examples/localization/example_bag was performed on this run: 3 verified, 0 mismatch, 0 not checked. Verified means the digest and the message position, topic and log time in the recording agree with the reference.

| Ref | Artifact | Message | Topic | Time | Clock domain | SHA-256 | Cited by | Verification |
|---|---|---|---|---|---|---|---|---|
| E1 | artifact\:bag_0.mcap | 0 | /rosout | 1700000000001000000 ns | mcap_log_time | c23b385766e8df16197cd124777c81a2b07f8ca18c3e78b9d65110b645b676ea | symptom s-c872a01752e24777 | verified |
| E2 | artifact\:bag_0.mcap | 1 | /diagnostics | 1700000000003000000 ns | mcap_log_time | c23b385766e8df16197cd124777c81a2b07f8ca18c3e78b9d65110b645b676ea | symptom s-4bd2015939fdeda6 | verified |

## Provenance

Report file: SHA-256 9c758f59edb4d49c05e958d7e7f1c638062ddd35f208aa55f0de1e3cd00676fe, 14867 byte(s).

Parameters hash: 8f7de5375d6284389d32e712f8cdc7c12e4777f223acd6714c1b8a3a1e3adefe.

Inputs: detector configuration 071df63bfe6bc19c3684692b2f523f0d3153871d68eaf03d3157386ef2551fe3; expectations none.

Dependencies: declared (1 edge(s)); file digest 54b257df81ce76dc8201d91d6f2459910c17934dacfd2ab15671ee29292cc255; semantic hash 9841db513ec37bde3b01d9a0029c1fe7ffcc77f2b14ab3cf69ad9aa5e01a9e0d.

- Evidence artifact artifact\:bag_0.mcap: SHA-256 c23b385766e8df16197cd124777c81a2b07f8ca18c3e78b9d65110b645b676ea.

End of report. Real-recording validation and decoder licensing clearance are outstanding; see the limitations.
