"""The built-in hypothesis catalogue, version 1: twelve entries, one per mechanism class that the M3c detectors can observe (plan, M4b).

Every entry is a candidate mechanism, written in fixed neutral wording. All entries are `proposed`: none counts as reviewed until the owner signs it off
(decision D12). The text below is parsed by the same loader and schema as a user extension file.
"""

CATALOGUE_V1_YAML = """\
catalogue_schema_version: 1
declared_by: "PhysiCore M4b built-in catalogue v1"
entries:
  - id: upstream_source_silent
    version: 1
    family: communication
    mechanism: "The publisher of a topic was silent for an interval while the recorder kept running, so consumers of that topic received no input during the interval."
    applies_to:
      symptom_categories: [publishing_gap, rate_change, diagnostic_status, log_severity]
    slots:
      - {name: silent_topics, selects: symptom_topics}
      - {name: consumers, selects: declared_downstream_topics}
      - {name: siblings, selects: sibling_topics}
    predictions:
      - {id: p1, text: "Consumers named by a declared edge show a symptom at or after the start of the silent interval.", rule: {op: symptom_present, slot: consumers, categories: [diagnostic_status, log_severity, rate_change, publishing_gap]}}
      - {id: p2, text: "Other topics of the same component show no symptom during the interval.", rule: {op: symptom_absent, slot: siblings, categories: [publishing_gap, rate_change]}}
      - {id: p3, text: "The silence ends or the recording ends; no message lies inside the interval.", rule: {op: not_evaluable}}
    checks: []
    evidence_requirements:
      - {token: node_liveness, description: "Whether the publishing process was running during the interval.", usual_availability: usually_absent}
      - {token: transport_statistics, description: "Counters of messages sent and received on the topic.", usual_availability: usually_absent}
      - {token: verified_dependency_direction, description: "Confirmation that the declared edge has the direction that the model states.", usual_availability: usually_absent}
    rivals: [message_loss_in_transport, software_exception_or_crash, recording_or_observation_artifact]
    review: {status: proposed, note: "Generic; a silence that never ends is invisible to the current detectors."}

  - id: message_loss_in_transport
    version: 1
    family: communication
    mechanism: "Messages that the publisher sent did not reach the recorder, while the publisher itself kept running."
    applies_to:
      symptom_categories: [publishing_gap, rate_change]
    slots:
      - {name: affected_topics, selects: symptom_topics}
      - {name: siblings, selects: sibling_topics}
    predictions:
      - {id: p1, text: "Other topics of the same component keep their normal rate during the interval.", rule: {op: symptom_absent, slot: siblings, categories: [publishing_gap, rate_change]}}
      - {id: p2, text: "Counters on the publisher side show messages sent that were not received.", rule: {op: not_evaluable}}
    checks: []
    evidence_requirements:
      - {token: transport_statistics, description: "Counters of messages sent and received on the topic.", usual_availability: usually_absent}
      - {token: recorder_drop_counters, description: "Recorder-side counters of dropped samples.", usual_availability: usually_absent}
    rivals: [upstream_source_silent, rate_degradation_under_load, recording_or_observation_artifact]
    review: {status: proposed, note: "Indistinguishable from a silent publisher without transport or recorder counters."}

  - id: rate_degradation_under_load
    version: 1
    family: timing
    mechanism: "The publisher kept running but delivered messages at a lower rate than its own baseline for a period."
    applies_to:
      symptom_categories: [rate_change, publishing_gap]
    slots:
      - {name: slow_topics, selects: symptom_topics}
      - {name: siblings, selects: sibling_topics}
    predictions:
      - {id: p1, text: "Other topics of the same component also show a lower rate in the same period.", rule: {op: symptom_present, slot: siblings, categories: [rate_change, publishing_gap]}}
      - {id: p2, text: "Processor, memory or bandwidth use was high in the same period.", rule: {op: not_evaluable}}
    checks: []
    evidence_requirements:
      - {token: cpu_load, description: "Processor load of the host over time.", usual_availability: usually_absent}
      - {token: memory_use, description: "Memory use of the host over time.", usual_availability: usually_absent}
    rivals: [resource_exhaustion, hardware_degradation, message_loss_in_transport]
    review: {status: proposed, note: "Needs host measurements to be assessed beyond the rate change itself."}

  - id: invalid_sensor_data_propagated
    version: 1
    family: data_validity
    mechanism: "A sensor topic carried values that are not valid numbers or lie outside declared bounds, and consumers of the topic received those values."
    applies_to:
      symptom_categories: [non_finite_value, m2_range, m2_tolerance, diagnostic_status, log_severity]
    slots:
      - {name: sensor_topics, selects: symptom_topics}
      - {name: consumers, selects: declared_downstream_topics}
    predictions:
      - {id: p1, text: "The values on the sensor topic violate the declared value range or are not finite.", rule: {op: check_violated, check: c1}}
      - {id: p2, text: "Consumers named by a declared edge show a symptom after the first invalid value.", rule: {op: symptom_present, slot: consumers, categories: [diagnostic_status, log_severity]}}
    checks:
      - {id: c1, kind: range, slot: sensor_topics, constraint_kind: value_range, description: "Values on the sensor topic stay inside the declared range."}
    evidence_requirements:
      - {token: driver_output, description: "Raw output of the sensor driver before publication.", usual_availability: usually_absent}
      - {token: calibration_state, description: "Calibration state of the sensor.", usual_availability: usually_absent}
    rivals: [hardware_degradation, configuration_mismatch, recording_or_observation_artifact]
    review: {status: proposed, note: "The range check runs only when the model declares a value range for the sensor binding."}

  - id: command_not_followed
    version: 1
    family: state
    mechanism: "A command was issued and the commanded state or response was not observed within the declared deadline."
    applies_to:
      symptom_categories: [m2_event_response, goal_status]
    slots:
      - {name: command_topics, selects: symptom_topics}
      - {name: responders, selects: declared_downstream_topics}
    predictions:
      - {id: p1, text: "The expected response does not appear on the responding topic within the declared deadline.", rule: {op: check_violated, check: c1}}
      - {id: p2, text: "Actuator feedback shows no motion after the command.", rule: {op: not_evaluable}}
    checks:
      - {id: c1, kind: event_response, slot: command_topics, constraint_kind: deadline, description: "The declared response follows the command within the declared deadline."}
    evidence_requirements:
      - {token: actuator_feedback, description: "Feedback from the actuator after the command.", usual_availability: usually_absent}
      - {token: controller_logs, description: "Logs of the component that receives the command.", usual_availability: usually_absent}
    rivals: [state_or_mode_transition_error, software_exception_or_crash, hardware_degradation]
    review: {status: proposed, note: "Matches only when an M2 contract with an event-response expectation was supplied to the localization step."}

  - id: state_or_mode_transition_error
    version: 1
    family: state
    mechanism: "A component entered a mode or state that does not match the transition sequence that was declared or commanded."
    applies_to:
      symptom_categories: [m2_transition, m2_equals, goal_status, diagnostic_status]
    slots:
      - {name: state_topics, selects: symptom_topics}
    predictions:
      - {id: p1, text: "The recorded sequence of states contains a transition that the expectation does not allow.", rule: {op: not_evaluable}}
    checks: []
    evidence_requirements:
      - {token: controller_logs, description: "Logs of the component that owns the state.", usual_availability: usually_absent}
    rivals: [command_not_followed, software_exception_or_crash, configuration_mismatch]
    review: {status: proposed, note: "The model has no transition constraint yet, so no automatic check exists."}

  - id: configuration_mismatch
    version: 1
    family: configuration
    mechanism: "A parameter or configuration value differs from what the consumers of a topic expect, so the shape or content of its messages changed."
    applies_to:
      symptom_categories: [array_length_change, m2_equals, diagnostic_status, log_severity]
    slots:
      - {name: changed_topics, selects: symptom_topics}
      - {name: consumers, selects: declared_downstream_topics}
    predictions:
      - {id: p1, text: "A declared parameter change lies shortly before the change in the messages.", rule: {op: not_evaluable}}
      - {id: p2, text: "Consumers named by a declared edge show a symptom after the change.", rule: {op: symptom_present, slot: consumers, categories: [diagnostic_status, log_severity]}}
    checks: []
    evidence_requirements:
      - {token: parameter_values, description: "Parameter values of the publisher and of its consumers over time.", usual_availability: usually_absent}
    rivals: [software_exception_or_crash, recording_or_observation_artifact]
    review: {status: proposed, note: "Declared interventions in the model are the only automatic hint; they are not yet compared with the change."}

  - id: timestamp_or_clock_fault
    version: 1
    family: timing
    mechanism: "Message timestamps moved backwards or repeated, which points at a clock source or stamping problem and not at the data content."
    applies_to:
      symptom_categories: [timestamp_regression, timestamp_duplicate]
    slots:
      - {name: stamped_topics, selects: symptom_topics}
      - {name: siblings, selects: sibling_topics}
    predictions:
      - {id: p1, text: "Receipt order on the recorder stays normal while the payload stamps move backwards.", rule: {op: not_evaluable}}
      - {id: p2, text: "Other topics of the same component show no timestamp symptom.", rule: {op: symptom_absent, slot: siblings, categories: [timestamp_regression, timestamp_duplicate]}}
    checks: []
    evidence_requirements:
      - {token: clock_source_status, description: "State of the clock source and of time synchronisation.", usual_availability: usually_absent}
    rivals: [recording_or_observation_artifact, software_exception_or_crash]
    review: {status: proposed, note: "Only payload stamps are examined; recorder receipt time is a separate clock domain."}

  - id: resource_exhaustion
    version: 1
    family: resource
    mechanism: "A shared resource such as processor time, memory, bandwidth or disk space was exhausted, which delayed or prevented work in several components."
    applies_to:
      symptom_categories: [rate_change, publishing_gap, diagnostic_status, log_severity]
    slots:
      - {name: affected_topics, selects: symptom_topics}
      - {name: siblings, selects: sibling_topics}
    predictions:
      - {id: p1, text: "Several unrelated topics of the same host show a lower rate or a gap in the same period.", rule: {op: symptom_present, slot: siblings, categories: [rate_change, publishing_gap]}}
      - {id: p2, text: "Host measurements show a resource at its limit in that period.", rule: {op: not_evaluable}}
    checks: []
    evidence_requirements:
      - {token: cpu_load, description: "Processor load of the host over time.", usual_availability: usually_absent}
      - {token: memory_use, description: "Memory use of the host over time.", usual_availability: usually_absent}
    rivals: [rate_degradation_under_load, hardware_degradation, software_exception_or_crash]
    review: {status: proposed, note: "Cannot be assessed from topic data alone; the missing measurements are named in every report."}

  - id: hardware_degradation
    version: 1
    family: hardware
    mechanism: "A physical device degraded in performance, and its outputs became slower, noisier or absent."
    applies_to:
      symptom_categories: [rate_change, publishing_gap, power_status, diagnostic_status, m2_range, m2_tolerance]
    slots:
      - {name: device_topics, selects: symptom_topics}
    predictions:
      - {id: p1, text: "Device telemetry such as current or temperature moved outside its normal band before the symptom.", rule: {op: not_evaluable}}
    checks: []
    evidence_requirements:
      - {token: motor_current, description: "Current drawn by the device over time.", usual_availability: usually_absent}
      - {token: motor_temperature, description: "Temperature of the device over time.", usual_availability: usually_absent}
    rivals: [rate_degradation_under_load, resource_exhaustion, software_exception_or_crash]
    review: {status: proposed, note: "Reported as cannot be assessed unless device telemetry is in the recording."}

  - id: software_exception_or_crash
    version: 1
    family: software_fault
    mechanism: "A process raised an unhandled exception or exited, which ended or interrupted its outputs."
    applies_to:
      symptom_categories: [publishing_gap, log_severity, diagnostic_status, goal_status]
    slots:
      - {name: affected_topics, selects: symptom_topics}
    predictions:
      - {id: p1, text: "A log message from the affected component reports an error or an exit near the start of the interval.", rule: {op: symptom_present, slot: affected_topics, categories: [log_severity]}}
      - {id: p2, text: "The process exit status shows a non-zero code or a signal.", rule: {op: not_evaluable}}
    checks: []
    evidence_requirements:
      - {token: process_exit_status, description: "Exit status or signal of the affected process.", usual_availability: usually_absent}
      - {token: node_liveness, description: "Whether the process was running during the interval.", usual_availability: usually_absent}
    rivals: [upstream_source_silent, message_loss_in_transport, resource_exhaustion]
    review: {status: proposed, note: "A process that ends without a log message leaves no trace in a recording."}

  - id: recording_or_observation_artifact
    version: 1
    family: observation_artifact
    mechanism: "The recorder or the order of receipt, and not the robot, produced the apparent symptom, for example through capture loss, receipt-time ordering or a recording boundary."
    applies_to:
      symptom_categories: [log_severity, diagnostic_status, goal_status, power_status, publishing_gap, rate_change, timestamp_regression, timestamp_duplicate, array_length_change, non_finite_value, m2_range, m2_tolerance, m2_equals, m2_transition, m2_event_response]
    slots:
      - {name: observed_topics, selects: symptom_topics}
    predictions:
      - {id: p1, text: "A repeat recording or recorder-side counters would not show the symptom.", rule: {op: not_evaluable}}
    checks: []
    evidence_requirements:
      - {token: recorder_drop_counters, description: "Recorder-side counters of dropped samples.", usual_availability: usually_absent}
      - {token: repeat_recording, description: "A second recording of the same situation.", usual_availability: usually_absent}
    rivals: [upstream_source_silent, message_loss_in_transport, timestamp_or_clock_fault]
    review: {status: proposed, note: "Always possible; kept as a candidate next to the observation-or-timing member that M3d always reports."}
"""
