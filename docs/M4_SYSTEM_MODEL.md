# The declared system model (M4a)

A system model is a short file in which the people who run a robot say what the robot is made of and what they expect from it. The investigation tools of
milestones M4b to M4e read it; this step only loads it, checks it against a recording, and reports the differences.

The tool never infers, completes or corrects a model. Everything in it is a declaration by the person named in `declared_by`; the tool cannot verify any of it.

## Command

```
python -m physicore_collector evidence system-model MODEL [--evidence PATH] [--dependencies PATH] [--json]
```

| Option | Meaning |
|---|---|
| `MODEL` | A YAML or JSON file, schema 1 (below). The file name decides the format: `.json` is JSON, anything else is YAML. |
| `--evidence PATH` | A recording (anything `evidence inventory` accepts). Without it the model is only validated. |
| `--dependencies PATH` | The dependency file whose digest the model names in `dependency_file_sha256`; the digests are compared. |
| `--json` | Machine-readable report. |

Exit codes: 0 model valid; 1 model valid, the recording is partial or unverified (the report is still produced); 2 the recording or dependency file cannot be read, or a usage error; 3 the model is invalid (nothing is compared; every problem is printed on standard error).

Nothing is written anywhere. Standard input is not accepted.

## The file

| Key | Required | Content |
|---|---|---|
| `system_model_schema_version` | yes | the integer `1` |
| `declared_by` | yes | who declares this file; items without their own `declared_by` inherit it |
| `system_name` | yes | a name |
| `operating_context` | no | free text up to 2000 characters, kept as declared |
| `dependency_file_sha256` | no | the SHA-256 of an existing M3d dependency file |
| `components` | no | `id`, `role` (sensor, controller, actuator, planner, driver, middleware, compute, power), optional `description` |
| `bindings` | no | `component`, `role` (a name you choose), `topic`, optional `field` (dotted path) and `type` (`pkg/msg/Name`) |
| `versions` | no | `component`, `kind` (software or hardware), `name`, `version`, optional `digest` |
| `constraints` | no | `value_range` (min and/or max), `rate` (min_hz and/or max_hz), `deadline` (trigger, response, deadline_ns), `redundancy_group` (members, min_healthy) |
| `interventions` | no | `parameter_change` or `restart`: `component`, `at_ns`, `clock_domain` (`mcap_log_time`) |
| `edges` | no | the M3d edge syntax; an endpoint is a topic (starts with `/`) or a declared component |

Every item may carry its own `declared_by`. The loaded model records, for each item, who declared it and whether that came from the item or from the file.

Rules: unknown keys are errors; references must resolve (a constraint names a binding, a binding names a component); ids are unique; numbers are finite; text has no control characters; files are limited to 1 MiB, and each list has a size limit. One problem anywhere makes the whole file invalid; nothing is repaired.

YAML is pre-scanned: anchors, aliases, tags, merge keys, duplicate keys, several documents and deep nesting are refused. JSON with duplicate keys or the constants NaN and Infinity is refused.

## The report

Two sections that are never merged: the model (marked `declared`, with the file digest and a digest of its content, which is the same for any two files that say the same thing) and the recording facts (marked `from_recording`: topics, message types and counts, and the time window).

Findings, each with fixed wording and a severity:

| Finding | Meaning |
|---|---|
| `declared_topic_not_recorded` | a topic the model uses is not in the recording |
| `recorded_topic_not_declared` | a recorded topic is not used in the model (a note) |
| `type_mismatch` | the recorded message type differs from the declared one |
| `constraint_cannot_be_evaluated` | a constraint needs a topic that is not recorded, a field that was not declared, or a binding that does not exist; the reason is named |
| `intervention_outside_recording` | a declared intervention lies outside the recording's time window |
| `dependency_file_digest_mismatch`, `dependency_file_not_supplied` | the dependency file does not match, or was not supplied |

A finding says that the model and the recording differ. It does not say which one is right, and no sentence offers an explanation. At most 200 findings are listed; the total is always given.

## Limits

- The model is only as good as its authors. An outdated model produces findings, not corrections.
- Only metadata the recording states itself is compared: topics, types, counts, time window. Quality-of-service settings are not exposed.
- Constraints are checked here only for whether they can be evaluated. Evaluating them is done by later steps.
- Real-recording validation of the readers and decoder licensing clearance are still outstanding.

An example with its output is in `examples/rootcause/`.
