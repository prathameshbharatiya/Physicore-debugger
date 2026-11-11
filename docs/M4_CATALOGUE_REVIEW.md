# M4 hypothesis catalogue: review pack

Generated from the built-in catalogue (digest `72ec7278514f6847b9dbef946d50903276221949b1df8b72179f17558b6d9302`). Do not edit by hand; a test fails if this file differs from the catalogue.

Each entry is a candidate mechanism, written as one neutral sentence. An entry is **proposed** until a named reviewer signs it off in the catalogue itself
(`review.status: reviewed`, with `reviewed_by` and `reviewed_on`). Until then this tool presents it as a candidate whose review is pending.

What to check, for each entry: (1) the sentence says only what the mechanism is; (2) the predictions are things a recording could show if the mechanism were
at work; (3) the evidence it names is what would separate it from its rivals; (4) its rivals are the mechanisms it is most often confused with.

Summary: 12 entries; 12 proposed, 0 reviewed.

## `command_not_followed`  (version 1, family state)

A command was issued and the commanded state or response was not observed within the declared deadline.

- Review status: proposed (Matches only when an M2 contract with an event-response expectation was supplied to the localization step.)
- Applies to symptom categories: goal_status; m2_event_response
- Slots: command_topics = symptom_topics; responders = declared_downstream_topics
- Predictions: p1: The expected response does not appear on the responding topic within the declared deadline. (rule: check_violated); p2: Actuator feedback shows no motion after the command. (rule: not_evaluable)
- Checks: c1: The declared response follows the command within the declared deadline. (M2 event_response, constraint deadline, slot command_topics)
- Evidence that would help: actuator_feedback: Feedback from the actuator after the command. (usually absent); controller_logs: Logs of the component that receives the command. (usually absent)
- Rivals: `hardware_degradation`; `software_exception_or_crash`; `state_or_mode_transition_error`

## `configuration_mismatch`  (version 1, family configuration)

A parameter or configuration value differs from what the consumers of a topic expect, so the shape or content of its messages changed.

- Review status: proposed (Declared interventions in the model are the only automatic hint; they are not yet compared with the change.)
- Applies to symptom categories: array_length_change; diagnostic_status; log_severity; m2_equals
- Slots: changed_topics = symptom_topics; consumers = declared_downstream_topics
- Predictions: p1: A declared parameter change lies shortly before the change in the messages. (rule: not_evaluable); p2: Consumers named by a declared edge show a symptom after the change. (rule: symptom_present)
- Checks: none
- Evidence that would help: parameter_values: Parameter values of the publisher and of its consumers over time. (usually absent)
- Rivals: `recording_or_observation_artifact`; `software_exception_or_crash`

## `hardware_degradation`  (version 1, family hardware)

A physical device degraded in performance, and its outputs became slower, noisier or absent.

- Review status: proposed (Reported as cannot be assessed unless device telemetry is in the recording.)
- Applies to symptom categories: diagnostic_status; m2_range; m2_tolerance; power_status; publishing_gap; rate_change
- Slots: device_topics = symptom_topics
- Predictions: p1: Device telemetry such as current or temperature moved outside its normal band before the symptom. (rule: not_evaluable)
- Checks: none
- Evidence that would help: motor_current: Current drawn by the device over time. (usually absent); motor_temperature: Temperature of the device over time. (usually absent)
- Rivals: `rate_degradation_under_load`; `resource_exhaustion`; `software_exception_or_crash`

## `invalid_sensor_data_propagated`  (version 1, family data_validity)

A sensor topic carried values that are not valid numbers or lie outside declared bounds, and consumers of the topic received those values.

- Review status: proposed (The range check runs only when the model declares a value range for the sensor binding.)
- Applies to symptom categories: diagnostic_status; log_severity; m2_range; m2_tolerance; non_finite_value
- Slots: consumers = declared_downstream_topics; sensor_topics = symptom_topics
- Predictions: p1: The values on the sensor topic violate the declared value range or are not finite. (rule: check_violated); p2: Consumers named by a declared edge show a symptom after the first invalid value. (rule: symptom_present)
- Checks: c1: Values on the sensor topic stay inside the declared range. (M2 range, constraint value_range, slot sensor_topics)
- Evidence that would help: calibration_state: Calibration state of the sensor. (usually absent); driver_output: Raw output of the sensor driver before publication. (usually absent)
- Rivals: `configuration_mismatch`; `hardware_degradation`; `recording_or_observation_artifact`

## `message_loss_in_transport`  (version 1, family communication)

Messages that the publisher sent did not reach the recorder, while the publisher itself kept running.

- Review status: proposed (Indistinguishable from a silent publisher without transport or recorder counters.)
- Applies to symptom categories: publishing_gap; rate_change
- Slots: affected_topics = symptom_topics; siblings = sibling_topics
- Predictions: p1: Other topics of the same component keep their normal rate during the interval. (rule: symptom_absent); p2: Counters on the publisher side show messages sent that were not received. (rule: not_evaluable)
- Checks: none
- Evidence that would help: recorder_drop_counters: Recorder-side counters of dropped samples. (usually absent); transport_statistics: Counters of messages sent and received on the topic. (usually absent)
- Rivals: `rate_degradation_under_load`; `recording_or_observation_artifact`; `upstream_source_silent`

## `rate_degradation_under_load`  (version 1, family timing)

The publisher kept running but delivered messages at a lower rate than its own baseline for a period.

- Review status: proposed (Needs host measurements to be assessed beyond the rate change itself.)
- Applies to symptom categories: publishing_gap; rate_change
- Slots: siblings = sibling_topics; slow_topics = symptom_topics
- Predictions: p1: Other topics of the same component also show a lower rate in the same period. (rule: symptom_present); p2: Processor, memory or bandwidth use was high in the same period. (rule: not_evaluable)
- Checks: none
- Evidence that would help: cpu_load: Processor load of the host over time. (usually absent); memory_use: Memory use of the host over time. (usually absent)
- Rivals: `hardware_degradation`; `message_loss_in_transport`; `resource_exhaustion`

## `recording_or_observation_artifact`  (version 1, family observation_artifact)

The recorder or the order of receipt, and not the robot, produced the apparent symptom, for example through capture loss, receipt-time ordering or a recording boundary.

- Review status: proposed (Always possible; kept as a candidate next to the observation-or-timing member that M3d always reports.)
- Applies to symptom categories: array_length_change; diagnostic_status; goal_status; log_severity; m2_equals; m2_event_response; m2_range; m2_tolerance; m2_transition; non_finite_value; power_status; publishing_gap; rate_change; timestamp_duplicate; timestamp_regression
- Slots: observed_topics = symptom_topics
- Predictions: p1: A repeat recording or recorder-side counters would not show the symptom. (rule: not_evaluable)
- Checks: none
- Evidence that would help: recorder_drop_counters: Recorder-side counters of dropped samples. (usually absent); repeat_recording: A second recording of the same situation. (usually absent)
- Rivals: `message_loss_in_transport`; `timestamp_or_clock_fault`; `upstream_source_silent`

## `resource_exhaustion`  (version 1, family resource)

A shared resource such as processor time, memory, bandwidth or disk space was exhausted, which delayed or prevented work in several components.

- Review status: proposed (Cannot be assessed from topic data alone; the missing measurements are named in every report.)
- Applies to symptom categories: diagnostic_status; log_severity; publishing_gap; rate_change
- Slots: affected_topics = symptom_topics; siblings = sibling_topics
- Predictions: p1: Several unrelated topics of the same host show a lower rate or a gap in the same period. (rule: symptom_present); p2: Host measurements show a resource at its limit in that period. (rule: not_evaluable)
- Checks: none
- Evidence that would help: cpu_load: Processor load of the host over time. (usually absent); memory_use: Memory use of the host over time. (usually absent)
- Rivals: `hardware_degradation`; `rate_degradation_under_load`; `software_exception_or_crash`

## `software_exception_or_crash`  (version 1, family software_fault)

A process raised an unhandled exception or exited, which ended or interrupted its outputs.

- Review status: proposed (A process that ends without a log message leaves no trace in a recording.)
- Applies to symptom categories: diagnostic_status; goal_status; log_severity; publishing_gap
- Slots: affected_topics = symptom_topics
- Predictions: p1: A log message from the affected component reports an error or an exit near the start of the interval. (rule: symptom_present); p2: The process exit status shows a non-zero code or a signal. (rule: not_evaluable)
- Checks: none
- Evidence that would help: node_liveness: Whether the process was running during the interval. (usually absent); process_exit_status: Exit status or signal of the affected process. (usually absent)
- Rivals: `message_loss_in_transport`; `resource_exhaustion`; `upstream_source_silent`

## `state_or_mode_transition_error`  (version 1, family state)

A component entered a mode or state that does not match the transition sequence that was declared or commanded.

- Review status: proposed (The model has no transition constraint yet, so no automatic check exists.)
- Applies to symptom categories: diagnostic_status; goal_status; m2_equals; m2_transition
- Slots: state_topics = symptom_topics
- Predictions: p1: The recorded sequence of states contains a transition that the expectation does not allow. (rule: not_evaluable)
- Checks: none
- Evidence that would help: controller_logs: Logs of the component that owns the state. (usually absent)
- Rivals: `command_not_followed`; `configuration_mismatch`; `software_exception_or_crash`

## `timestamp_or_clock_fault`  (version 1, family timing)

Message timestamps moved backwards or repeated, which points at a clock source or stamping problem and not at the data content.

- Review status: proposed (Only payload stamps are examined; recorder receipt time is a separate clock domain.)
- Applies to symptom categories: timestamp_duplicate; timestamp_regression
- Slots: siblings = sibling_topics; stamped_topics = symptom_topics
- Predictions: p1: Receipt order on the recorder stays normal while the payload stamps move backwards. (rule: not_evaluable); p2: Other topics of the same component show no timestamp symptom. (rule: symptom_absent)
- Checks: none
- Evidence that would help: clock_source_status: State of the clock source and of time synchronisation. (usually absent)
- Rivals: `recording_or_observation_artifact`; `software_exception_or_crash`

## `upstream_source_silent`  (version 1, family communication)

The publisher of a topic was silent for an interval while the recorder kept running, so consumers of that topic received no input during the interval.

- Review status: proposed (Generic; a silence that never ends is invisible to the current detectors.)
- Applies to symptom categories: diagnostic_status; log_severity; publishing_gap; rate_change
- Slots: consumers = declared_downstream_topics; siblings = sibling_topics; silent_topics = symptom_topics
- Predictions: p1: Consumers named by a declared edge show a symptom at or after the start of the silent interval. (rule: symptom_present); p2: Other topics of the same component show no symptom during the interval. (rule: symptom_absent); p3: The silence ends or the recording ends; no message lies inside the interval. (rule: not_evaluable)
- Checks: none
- Evidence that would help: node_liveness: Whether the publishing process was running during the interval. (usually absent); transport_statistics: Counters of messages sent and received on the topic. (usually absent); verified_dependency_direction: Confirmation that the declared edge has the direction that the model states. (usually absent)
- Rivals: `message_loss_in_transport`; `recording_or_observation_artifact`; `software_exception_or_crash`
