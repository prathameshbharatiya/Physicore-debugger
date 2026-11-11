# Investigator (Phase 2): deterministic first divergence

Compares **explicitly supplied expected behaviour** with the evidence described by the Phase 1 inventory and reports the earliest
divergence that can be **defended**. It does not diagnose, rank causes, score confidence or suggest fixes. Read-only; nothing is
written into the evidence, and no investigation record is added to a package.

```
physicore-collector evidence investigate <PATH> --expectations <contract.json|yaml> [--json]
```
```python
from physicore_collector.inventory import inspect_evidence
from physicore_collector.investigator import load_contract, investigate, investigation_json
result = investigate(inspect_evidence("path/to/evidence"), load_contract("expectations.json"))   # never raises for bad content
print(investigation_json(result))                                                                 # deterministic JSON
```
Exit codes: **0** no divergence observed in the covered window · **1** divergence found · **2** inconclusive (insufficient evidence
or incomparable timebases) · **3** the contract is invalid.

Example (committed, regenerated and compared by a test): `examples/investigator/` holds a synthetic recording, a contract, the exact JSON
output and the readable report. Run it:

```
physicore-collector evidence investigate examples/investigator/example_recording.mcap -e examples/investigator/expectations.json
```

## Design

```
 evidence (incident / package / bag / .mcap) ──► evidence inventory (Phase 1, unchanged) ─┐
                                                                                          ├─► investigator ─► InvestigationResult
 expectation contract (explicit, versioned) ───────────────────────────────────────────────┘
```
The Investigator imports from the Collector and is imported by nothing in it except the one CLI command (`cli.py`, additive).

* `investigator/contract.py` – contract schema 1.0, loader, canonical digest.
* `investigator/observations.py` – canonical `Observation`, segment selection and message reading.
* `investigator/engine.py` – evaluation, ordering, result schema 1.0, JSON/report/exit code.

**Reused:** the inventory (artifact references, integrity verdicts, segment states, message counts, log_time windows – all from the
Collector's own scan/manifest), `first_symlink_component`, the Collector's bounded file reader and MCAP error taxonomy, the `_Strict`
pydantic base, `sha256_file`.
**New, and why:** the Collector deliberately never decodes payloads (`mcap_reader.NOT_VERIFIED`), so reading a *value* needs a small
record-level iterator over the same `mcap` library. It is not a second structural parser: a segment is used only if the inventory says it
is readable/partially readable, its artifact integrity is `verified`/`not_applicable`, its bytes still hash to the inventory's digest, and
the number of messages read equals the inventory's count. Otherwise the segment is `unusable` and the reason is reported.

## Input contract (schema 1.0)

```yaml
contract_schema_version: "1.0"
contract_id: my-contract
expectations:
  - expectation_id: ARM-TEMP-LIMIT            # unique; [A-Za-z0-9][A-Za-z0-9_.:-]*
    description: While RUNNING the temperature must not exceed 70.0
    topic: /arm/temperature
    signal: celsius                            # dotted JSON path; an all-digit segment indexes a list ("position.0")
    clock_domain: mcap_log_time                # the domain the window is written in (see Clocks)
    window: {start_ns: 0, end_ns: 5000000000}  # optional, inclusive, either end optional
    applies_when:                              # optional, ANDed; latest EARLIER sample of that signal must satisfy it
      - {topic: /arm/mode, signal: state, op: eq, value: RUNNING}     # op: eq ne lt le gt ge
    check: {kind: range, max: 70.0}            # see Supported comparisons
    source: {kind: specification, reference: SPEC-4.2, description: optional}   # REQUIRED: operator_supplied | specification | test_fixture
```
Nothing is inferred from a recording: there is no "learn the normal range" mode. The whole contract is rejected (`invalid_expectation`,
nothing evaluated) if any expectation is invalid: unknown fields, missing source, duplicate ids, a number written as text, a bool where a
number is needed, reversed bounds/window, negative tolerance, `min > max`, a transition with equal ends or a zero deadline, bad signal
paths, `applies_when` with a payload clock, wrong `contract_schema_version`, oversized/unreadable/symlinked contract files. JSON and YAML
are accepted; numbers are parsed as **exact decimals**.

### Supported comparisons

| `check.kind` | Meaning | Divergence time |
|---|---|---|
| `range` (`min`, `max`; one or both) | value in `[min, max]`, inclusive | the first violating sample |
| `tolerance` (`target`, `tolerance`) | `abs(value - target) <= tolerance`, inclusive | the first violating sample |
| `equals` (`value`: string, bool or number) | same type and equal (`"5"` is not `5`) | the first violating sample |
| `transition` (`from_value`, `to_value`, `deadline_ns`) | after a sample **enters** `from_value`, a sample equal to `to_value` must occur strictly later and no later than `deadline_ns` after it | the deadline instant (`time.kind = deadline_expiry`) |

Values are compared in the units the message carries; there is no unit conversion, arithmetic across signals, or expression language.
Only `json`-encoded payloads are decoded; other encodings (CDR, …) give `insufficient_evidence` (`payload_encoding_not_supported`).

## Output (`InvestigationResult`, schema 1.0)

`status` (one of five) · `status_reason_code` / `status_reason` · `first_divergence` (only for `divergence_found`) · `candidates` (every
confirmed mismatch, earliest first) · `expectation_results` (per expectation, contract order: status, reason, covered window, counts,
blind spots, missing evidence) · `contract` (id, digest) · `contract_problems` · `evidence` (inventory states, per-segment use, coverage) ·
`not_evaluated` · `not_established` (standing statements) · `provenance` (`read_only: true`, `root_cause_claimed: false`,
`live_ros2_capture_verified: false`).

| `status` | Meaning |
|---|---|
| `divergence_found` | A mismatch is established **and** the earliest can be defended. |
| `no_divergence_observed_in_covered_window` | No mismatch was established in what was evaluated (every expectation evaluated, evidence complete). **Not** proof the system is correct. |
| `insufficient_evidence` | Evidence missing, truncated, unreadable, changed, undecodable or not covering the window; **or** a mismatch exists but which came first cannot be established (`earliest_divergence_not_defensible`: its `candidates` are still listed, with `ordering`). |
| `incomparable_timebases` | An expectation's window clock is not the clock of its samples, or confirmed mismatches sit on clocks that cannot be ordered against each other. |
| `invalid_expectation` | The contract does not validate. Nothing was evaluated. |

A `Finding` carries: `finding_id` (hash of the expectation, the segment bytes and the observation – stable for the same inputs),
`expectation_id`, `expected`, `observed`, `discrepancy`, `time` (ns, clock domain, timestamp basis, kind), `rule` (comparison text,
parameters, tolerance used), `sources` (artifact ref, relative path, artifact sha256, topic, channel, observation id, role),
`observations` (canonical records), `explanation`, `uncertainty`, `ordering`.

An `Observation` carries: `observation_id` (`obs:<artifact path>#<message index in file>`), `artifact_ref`, `topic`, `signal`, `value` +
`value_type`, `timestamp_ns`, `timestamp_basis`, `clock_domain`, `quality` (`ok`, `payload_undecodable`, `signal_missing`,
`type_unsupported`, `timestamp_missing`), `parser_status` (the Collector's readability verdict for the segment).

## Rules (each has tests in `tests/test_investigator.py`)

* **Clocks.** Samples are in `mcap_log_time`, or in `payload_stamp:<topic>:<field>` when the contract names a payload timestamp field.
  Domains are comparable only if identical. Nothing is converted, shifted or "matched by magnitude". Mismatches on two domains →
  `incomparable_timebases`; a window declared in another domain → that expectation is `incomparable_timebases`.
* **Equal timestamps are not ordered.** Tied divergences, a prerequisite sampled at the same instant as the sample, and a transition whose ends
  share a timestamp are reported as ambiguous/blind spots, never resolved by file order.
* **Blind spots.** A sample that cannot be evaluated (undecodable, signal missing, wrong type, no timestamp, unknown prerequisite) is recorded.
  A blind spot at or before a mismatch (on the same clock) means that mismatch is not claimed as the earliest; any blind spot in the window
  prevents `no_divergence…`.
* **Incomplete evidence** (partially readable, missing or changed segment, count mismatch) never gives `no_divergence…` and never an earliest
  claim; the recovered samples are still evaluated and a mismatch among them is reported as a candidate.
* **Unevaluable expectations** do not hide a confirmed mismatch; they are listed in its `uncertainty` (an earlier divergence among them
  cannot be excluded).
* **Segments are merged by timestamp**, not file order. A tolerance/window boundary is exact (decimal arithmetic, inclusive).
* **Read-only and deterministic.** No writes, renames or locks (tested with a write guard); no wall-clock time or host path in the output.

## Limitations and deferred work

* Not verified against real ROS 2 output: tests use synthetic MCAP files with JSON payloads. Real rosbag2 recordings are mostly CDR; decoding
  CDR needs message definitions and is deferred. Such topics currently yield `insufficient_evidence`.
* "Earliest" is earliest **among evaluated expectations**. Expectations that could not be evaluated are disclosed, not assumed clean.
* The only time relations supported are within one clock domain. There is no mechanism (yet) to attest that two domains are comparable
  or to supply an offset; that would have to be explicit, attested and recorded, and is deferred.
* Transition semantics are minimal: entry into `from_value` is a change from a different value (or the first sample); `to_value` must be seen
  by a sample (a signal that changes between samples cannot be seen). A deadline that extends past the recording is undecidable, not a mismatch.
* Absence of messages is evidence only when the recording is complete and the deadline lies within it; a stopped publisher and a dropped message
  are indistinguishable here (rosbag2 exposes no drop counters).
* Prerequisites use the latest earlier sample (sample-and-hold); no interpolation, no staleness limit.
* All decoded observations for the requested topics are held in memory (guard: 2,000,000 messages per investigation; beyond it the segment is not used and the evidence is reported incomplete). Large-bag performance was not measured.
* `evidence_root` is derived from the path the inventory was made from; an inventory moved to another machine or working directory needs
  `investigate(..., evidence_root=...)`.
* TOCTOU: the symlink and digest checks happen at investigation time; a file swapped during the read is not prevented.
* Findings are not persisted into the package/incident (deferred by design). No historical retrieval, no RRIE, no LLM.
