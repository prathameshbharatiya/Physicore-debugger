# Hypothesis report

Candidate mechanisms for the symptoms in a localization report, with the evidence for and against each\. Each candidate is a proposal from a closed catalogue; none is called the cause\.

Scope\: This is the earliest symptom observed by the detectors that ran, under their configured parameters, using recorder receipt time\. It is not a statement about which event occurred first in the robot\.

The level printed beside a hypothesis is the highest level whose evidence is fully present\. It is not a measure of how likely the mechanism is\. Level 5 needs a deliberate test on the system and is never reached here\.

Counts\: 1 investigation unit\(s\), 8 hypothesis instance\(s\), 0 leading\. Sets\: leading 0, competing 0, unresolved 1, no_applicable_mechanism 0\. Levels\: 0 8, 1 0, 2 0, 3 0, 4 0\.

Catalogue digest 72ec7278514f6847b9dbef946d50903276221949b1df8b72179f17558b6d9302\. Entries used, by review status\: proposed 8, reviewed 0, user_supplied 0\.

## Notes on the inputs

- The report was made with a dependency file that was not supplied here, so declared neighbours are bound from the system model only\.

## Unit 1 of 1\: /diagnostics, /rosout

Set status\: unresolved \(nothing_supported\)\. The available evidence does not separate the candidate mechanisms, and it is not enough to choose among them\.

Symptoms in this unit\: s-4bd2015939fdeda6, s-c872a01752e24777\. Categories\: diagnostic_status, log_severity\.

| Order | Entry | Tier | Claim level | Satisfied share | Review status |
|---|---|---|---|---|---|
| 1 | hardware_degradation | not_assessed | 0 \(observed_order\) | 0/0 | proposed |
| 2 | recording_or_observation_artifact | not_assessed | 0 \(observed_order\) | 0/0 | proposed |
| 3 | software_exception_or_crash | not_assessed | 0 \(observed_order\) | 0/0 | proposed |
| 4 | state_or_mode_transition_error | not_assessed | 0 \(observed_order\) | 0/0 | proposed |
| 5 | configuration_mismatch | not_assessed | 0 \(observed_order\) | 0/1 | proposed |
| 6 | invalid_sensor_data_propagated | not_assessed | 0 \(observed_order\) | 0/2 | proposed |
| 7 | resource_exhaustion | not_assessed | 0 \(observed_order\) | 0/1 | proposed |
| 8 | upstream_source_silent | not_assessed | 0 \(observed_order\) | 0/2 | proposed |

### hardware_degradation \(order 1\)

A physical device degraded in performance, and its outputs became slower, noisier or absent\.

**Level 0\: The symptoms of this unit were observed in the recording in the order listed\.**

What would raise it\: A declared dependency edge and window in which the order of the symptoms fits would raise this to level 1\.

- p1\: Device telemetry such as current or temperature moved outside its normal band before the symptom\. State\: not_evaluable\.

| Item | Kind | Stance | Detail |
|---|---|---|---|
| h-91fb96bbeeb96ec4\#1 | unconfirmed | missing | measurement not available\: motor_current |
| h-91fb96bbeeb96ec4\#2 | unconfirmed | missing | measurement not available\: motor_temperature |

Bindings\: device_topics \= /diagnostics, /rosout \(m3d_report\)\.

### recording_or_observation_artifact \(order 2\)

The recorder or the order of receipt, and not the robot, produced the apparent symptom, for example through capture loss, receipt-time ordering or a recording boundary\.

**Level 0\: The symptoms of this unit were observed in the recording in the order listed\.**

What would raise it\: A declared dependency edge and window in which the order of the symptoms fits would raise this to level 1\.

- p1\: A repeat recording or recorder-side counters would not show the symptom\. State\: not_evaluable\.

| Item | Kind | Stance | Detail |
|---|---|---|---|
| h-e4168debf41ed025\#1 | unconfirmed | missing | measurement not available\: recorder_drop_counters |
| h-e4168debf41ed025\#2 | unconfirmed | missing | measurement not available\: repeat_recording |

Bindings\: observed_topics \= /diagnostics, /rosout \(m3d_report\)\.

### software_exception_or_crash \(order 3\)

A process raised an unhandled exception or exited, which ended or interrupted its outputs\.

**Level 0\: The symptoms of this unit were observed in the recording in the order listed\.**

What would raise it\: A declared dependency edge and window in which the order of the symptoms fits would raise this to level 1\.

- p1\: A log message from the affected component reports an error or an exit near the start of the interval\. State\: restates_symptom\.
- p2\: The process exit status shows a non-zero code or a signal\. State\: not_evaluable\.

| Item | Kind | Stance | Detail |
|---|---|---|---|
| h-129c06091525dad1\#1 | unconfirmed | missing | measurement not available\: node_liveness |
| h-129c06091525dad1\#2 | unconfirmed | missing | measurement not available\: process_exit_status |

Bindings\: affected_topics \= /diagnostics, /rosout \(m3d_report\)\.

### state_or_mode_transition_error \(order 4\)

A component entered a mode or state that does not match the transition sequence that was declared or commanded\.

**Level 0\: The symptoms of this unit were observed in the recording in the order listed\.**

What would raise it\: A declared dependency edge and window in which the order of the symptoms fits would raise this to level 1\.

- p1\: The recorded sequence of states contains a transition that the expectation does not allow\. State\: not_evaluable\.

| Item | Kind | Stance | Detail |
|---|---|---|---|
| h-b71a197126c0f92a\#1 | unconfirmed | missing | measurement not available\: controller_logs |

Bindings\: state_topics \= /diagnostics, /rosout \(m3d_report\)\.

### configuration_mismatch \(order 5\)

A parameter or configuration value differs from what the consumers of a topic expect, so the shape or content of its messages changed\.

**Level 0\: The symptoms of this unit were observed in the recording in the order listed\.**

What would raise it\: A declared dependency edge and window in which the order of the symptoms fits would raise this to level 1\.

- p1\: A declared parameter change lies shortly before the change in the messages\. State\: not_evaluable\.
- p2\: Consumers named by a declared edge show a symptom after the change\. State\: binding_missing\.

| Item | Kind | Stance | Detail |
|---|---|---|---|
| h-d5e38b1a51c193a5\#1 | unconfirmed | missing | measurement not available\: parameter_values |

Bindings\: changed_topics \= /diagnostics, /rosout \(m3d_report\); consumers \= not bound \(not_bound\)\.

### invalid_sensor_data_propagated \(order 6\)

A sensor topic carried values that are not valid numbers or lie outside declared bounds, and consumers of the topic received those values\.

**Level 0\: The symptoms of this unit were observed in the recording in the order listed\.**

A check of this mechanism could not be run or answered, so the level is capped at 1\.

What would raise it\: A declared dependency edge and window in which the order of the symptoms fits would raise this to level 1\.

- p1\: The values on the sensor topic violate the declared value range or are not finite\. State\: check_open\.
- p2\: Consumers named by a declared edge show a symptom after the first invalid value\. State\: binding_missing\.

| Item | Kind | Stance | Detail |
|---|---|---|---|
| h-7a997b35c05af7c7\#1 | unconfirmed | missing | check c1\: not_runnable \(no_constraint\) |
| h-7a997b35c05af7c7\#2 | unconfirmed | missing | measurement not available\: calibration_state |
| h-7a997b35c05af7c7\#3 | unconfirmed | missing | measurement not available\: driver_output |

Bindings\: consumers \= not bound \(not_bound\); sensor_topics \= /diagnostics, /rosout \(m3d_report\)\.

### resource_exhaustion \(order 7\)

A shared resource such as processor time, memory, bandwidth or disk space was exhausted, which delayed or prevented work in several components\.

**Level 0\: The symptoms of this unit were observed in the recording in the order listed\.**

What would raise it\: A declared dependency edge and window in which the order of the symptoms fits would raise this to level 1\.

- p1\: Several unrelated topics of the same host show a lower rate or a gap in the same period\. State\: binding_missing\.
- p2\: Host measurements show a resource at its limit in that period\. State\: not_evaluable\.

| Item | Kind | Stance | Detail |
|---|---|---|---|
| h-6c6f4a70e55d3657\#1 | unconfirmed | missing | measurement not available\: cpu_load |
| h-6c6f4a70e55d3657\#2 | unconfirmed | missing | measurement not available\: memory_use |

Bindings\: affected_topics \= /diagnostics, /rosout \(m3d_report\); siblings \= not bound \(not_bound\)\.

### upstream_source_silent \(order 8\)

The publisher of a topic was silent for an interval while the recorder kept running, so consumers of that topic received no input during the interval\.

**Level 0\: The symptoms of this unit were observed in the recording in the order listed\.**

What would raise it\: A declared dependency edge and window in which the order of the symptoms fits would raise this to level 1\.

- p1\: Consumers named by a declared edge show a symptom at or after the start of the silent interval\. State\: binding_missing\.
- p2\: Other topics of the same component show no symptom during the interval\. State\: binding_missing\.
- p3\: The silence ends or the recording ends; no message lies inside the interval\. State\: not_evaluable\.

| Item | Kind | Stance | Detail |
|---|---|---|---|
| h-fb0153c89b505d96\#1 | unconfirmed | missing | measurement not available\: node_liveness |
| h-fb0153c89b505d96\#2 | unconfirmed | missing | measurement not available\: transport_statistics |
| h-fb0153c89b505d96\#3 | unconfirmed | missing | measurement not available\: verified_dependency_direction |

Bindings\: consumers \= not bound \(not_bound\); siblings \= not bound \(not_bound\); silent_topics \= /diagnostics, /rosout \(m…

### Evidence that would help

- calibration_state
- controller_logs
- cpu_load
- driver_output
- memory_use
- motor_current
- motor_temperature
- node_liveness
- parameter_values
- process_exit_status
- recorder_drop_counters
- repeat_recording
- transport_statistics
- verified_dependency_direction

### Diagnostic asks \(nothing here runs a test\)

| Entry | Measurement | Separates it from | Meaning |
|---|---|---|---|
| invalid_sensor_data_propagated | calibration_state\: Calibration state of the sensor\. | configuration_mismatch, hardware_degradation, recording_or_observation_artifact | If this measurement is available and shows what the mechanism predicts, its level can rise; if it shows the opposite, the mechanism is contradicted\. |
| state_or_mode_transition_error | controller_logs\: Logs of the component that owns the state\. | command_not_followed, configuration_mismatch, software_exception_or_crash | If this measurement is available and shows what the mechanism predicts, its level can rise; if it shows the opposite, the mechanism is contradicted\. |
| resource_exhaustion | cpu_load\: Processor load of the host over time\. | hardware_degradation, rate_degradation_under_load, software_exception_or_crash | If this measurement is available and shows what the mechanism predicts, its level can rise; if it shows the opposite, the mechanism is contradicted\. |
| invalid_sensor_data_propagated | driver_output\: Raw output of the sensor driver before publication\. | configuration_mismatch, hardware_degradation, recording_or_observation_artifact | If this measurement is available and shows what the mechanism predicts, its level can rise; if it shows the opposite, the mechanism is contradicted\. |
| resource_exhaustion | memory_use\: Memory use of the host over time\. | hardware_degradation, rate_degradation_under_load, software_exception_or_crash | If this measurement is available and shows what the mechanism predicts, its level can rise; if it shows the opposite, the mechanism is contradicted\. |
| hardware_degradation | motor_current\: Current drawn by the device over time\. | rate_degradation_under_load, resource_exhaustion, software_exception_or_crash | If this measurement is available and shows what the mechanism predicts, its level can rise; if it shows the opposite, the mechanism is contradicted\. |
| hardware_degradation | motor_temperature\: Temperature of the device over time\. | rate_degradation_under_load, resource_exhaustion, software_exception_or_crash | If this measurement is available and shows what the mechanism predicts, its level can rise; if it shows the opposite, the mechanism is contradicted\. |
| software_exception_or_crash | node_liveness\: Whether the process was running during the interval\. | message_loss_in_transport, resource_exhaustion, upstream_source_silent | If this measurement is available and shows what the mechanism predicts, its level can rise; if it shows the opposite, the mechanism is contradicted\. |
| upstream_source_silent | node_liveness\: Whether the publishing process was running during the interval\. | message_loss_in_transport, recording_or_observation_artifact, software_exception_or_crash | If this measurement is available and shows what the mechanism predicts, its level can rise; if it shows the opposite, the mechanism is contradicted\. |
| configuration_mismatch | parameter_values\: Parameter values of the publisher and of its consumers over time\. | recording_or_observation_artifact, software_exception_or_crash | If this measurement is available and shows what the mechanism predicts, its level can rise; if it shows the opposite, the mechanism is contradicted\. |
| software_exception_or_crash | process_exit_status\: Exit status or signal of the affected process\. | message_loss_in_transport, resource_exhaustion, upstream_source_silent | If this measurement is available and shows what the mechanism predicts, its level can rise; if it shows the opposite, the mechanism is contradicted\. |
| recording_or_observation_artifact | recorder_drop_counters\: Recorder-side counters of dropped samples\. | message_loss_in_transport, timestamp_or_clock_fault, upstream_source_silent | If this measurement is available and shows what the mechanism predicts, its level can rise; if it shows the opposite, the mechanism is contradicted\. |
| recording_or_observation_artifact | repeat_recording\: A second recording of the same situation\. | message_loss_in_transport, timestamp_or_clock_fault, upstream_source_silent | If this measurement is available and shows what the mechanism predicts, its level can rise; if it shows the opposite, the mechanism is contradicted\. |
| upstream_source_silent | transport_statistics\: Counters of messages sent and received on the topic\. | message_loss_in_transport, recording_or_observation_artifact, software_exception_or_crash | If this measurement is available and shows what the mechanism predicts, its level can rise; if it shows the opposite, the mechanism is contradicted\. |
| upstream_source_silent | verified_dependency_direction\: Confirmation that the declared edge has the direction that the model states\. | message_loss_in_transport, recording_or_observation_artifact, software_exception_or_crash | If this measurement is available and shows what the mechanism predicts, its level can rise; if it shows the opposite, the mechanism is contradicted\. |

### Declared edges at this unit \(from the localization step's coverage\)

- Edge e1\: /rosout to /diagnostics\. Both endpoints of the declared edge show symptoms\.

## The claim ladder

| Level | Name | Wording | Reachable here |
|---|---|---|---|
| 0 | observed_order | The symptoms of this unit were observed in the recording in the order listed\. | yes |
| 1 | consistent_with_declared_dependency | The order fits a declared dependency edge and window, which is consistent with propagation along the declared edge\. | yes |
| 2 | compatible_with_mechanism | The evidence is compatible with this mechanism; it is supported by the listed evidence\. | yes |
| 3 | alternatives_excluded_within_coverage | The recorded evidence favours this mechanism over the listed alternatives\. | yes |
| 4 | difference_evidence | The evidence indicates this mechanism, supported by a contrast between these recordings\. | yes |
| 5 | controlled_intervention | \(none\: not reachable\) | no |

## Limits

- Tiers order candidates by how many of their predictions the evidence meets\. They are not probabilities\.
- A prediction that the evidence does not meet stays open; the report cannot show that nothing happened on a topic\.
- Results on the synthetic corpus show that the software follows its definitions, not accuracy on real robots\.
- Real-recording validation and decoder licensing clearance are still outstanding\.
- The catalogue entries are proposals until a named reviewer has signed each one off; the review status of every entry is shown\.
- The level printed next to a hypothesis is the highest level whose evidence is fully present\. It is not a measure of how likely the mechanism is\.
