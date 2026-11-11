# M3b — cross-topic event–response correlation

Built on the accepted M3a tree (kept intact in a separate copy; this document describes revision 2). Synthetic evidence only: no real robot recording has been used, and nothing here is
real-world validation. Not implemented, by instruction: anomaly detectors, causal analysis, LLM diagnosis, retrieval, incident memory.

**Revision 2.** Two further corrections: `stamp_type_check` now reports what the embedded schema actually establishes about the selected field (not merely that a schema exists), and the missing-response wording is limited to what a recording can establish (see "What a missing response means"). **Revision 1 (after review).** The original proposal specifies `event_response` on `mcap_log_time` for both trigger and response; this revision aligns with it. The check
measures **recorder receipt order, not when events physically occurred**. Corrections made: (1) the response state at the trigger must be observed; (2) entry into the trigger
condition must be observed; (3) event_response is `mcap_log_time` only, with no payload clocks or cross-clock attestations; (4) a zero `header.stamp` is accepted, and JSON stamp
types are not claimed to be verified; (5) the standing statement about decoding follows what was actually decoded.

## The check

```yaml
expectation_id: ER-ESTOP
topic: /estop            # the TRIGGER stream (this expectation's own topic/signal/time_source/window/applies_when)
signal: data
clock_domain: mcap_log_time      # event_response requires time_source log_time (the default)
check:
  kind: event_response
  trigger_op: eq         # an event = a sample satisfying this whose OBSERVED predecessor did not (a first sample already satisfying it is not an event)
  trigger_value: true
  response: {topic: /cmd_vel, signal: linear.x, op: eq, value: 0}      # always read on mcap_log_time
  deadline_ns: 200000000
applies_when:
  - {topic: /mode, signal: data, op: eq, value: AUTO, max_age_ns: 600000000}
```

For each trigger event at `t0` (inside the window, prerequisites holding at `t0`), a response is a response-stream sample that satisfies its condition with
`t0 < t_response <= t0 + deadline_ns`, compared inside one clock domain. The deadline is inclusive; "strictly later" excludes the same instant.

| Situation | Result |
|---|---|
| response in time | the event is answered; no finding |
| response received later than the deadline, or no matching response observed in the recorded stream, and the recording covers the deadline | divergence at `t0 + deadline` (`deadline_expiry`); a late response, if any, is cited as `late_response` |
| a matching response-stream sample at exactly `t0` | **undecided** (`event_response_ordering_ambiguous`); a later matching sample is not accepted as the response either |
| no response-stream sample before `t0` | undecided (`response_state_unknown`): the state at the trigger is unknown, so neither a later match is a confirmed response nor a later absence a confirmed miss |
| response condition already true before `t0` | undecided (`response_already_satisfied`): a later matching sample cannot be attributed to the trigger |
| response state before `t0` unreadable | undecided (`response_state_unknown`) |
| undecodable / wrong-typed response sample that could have answered | undecided; a missing response is not claimed |
| deadline beyond complete evidence on the response clock | undecided (`deadline_beyond_covered_evidence`) |
| first trigger-stream sample already satisfies the trigger | not an event; blind spot `event_entry_unknown` (nothing observed shows entry); not counted in `trigger_event_count` |
| two trigger samples at one instant; unreadable previous trigger sample | undecided (`event_ordering_ambiguous`, `event_entry_unknown`) |
| trigger never occurs (while prerequisites hold) | `insufficient_evidence` / `no_trigger_event_in_window` — not a pass |
| response topic or trigger topic has no messages at all | `insufficient_evidence` / `required_observations_missing` — a silent or unrecorded topic is not proof the event did not happen |

Undecided events become blind spots, which (as in Phase 2) block "no divergence" and block defending an earliest divergence. Results carry
`trigger_event_count` (entries found) and `response_matched_count`; they are omitted from other checks' JSON.

## max_age_ns (prerequisite freshness)

`applies_when[].max_age_ns` (positive integer): the latest earlier sample must be at most this old at the evaluated instant (inclusive). Older ⇒ the prerequisite
is **undecided** (`prerequisite_stale`, a blind spot), never "assumed to persist" and never "does not apply". Same-instant ambiguity is unchanged and checked
first. Works for every check kind; without it behaviour is exactly Phase 2's. Prerequisites remain `log_time`-only.

## header.stamp (single-signal checks only)

`time_source: header_stamp` on an ordinary expectation (range, tolerance, equals, transition), field default `header.stamp`: `ns = sec * 10^9 + nanosec`, exact integers. Domain
`payload_stamp:<topic>:<field>`, ordered by stamp inside that domain, never compared with `log_time` or another payload clock.
Not used (the sample gets `timestamp_missing`, an unplaced blind spot): negative `sec`, `nanosec` outside `[0, 10^9)`, non-integer or non-numeric parts, a missing field, and a field the embedded schema types as
something else (a `Duration` has the same shape and is refused). **A stamp of exactly zero is accepted as zero** — it can be a valid simulation time, and this tool does not decide it means "unset". There is no
"treat zero as unset" option in this milestone; if a stack uses zero for unstamped messages, use a window that excludes it.

**Type verification (`stamp_type_check`).** Reported per expectation, from what the embedded schema establishes about the *selected field*, not merely that a schema exists:

| Label | Meaning |
|---|---|
| `schema_verified` | every message of the topic is CDR, decoded, and the schema declares exactly this field as `builtin_interfaces/msg/Time` |
| `rejected_type` | the schema declares the field as something else (e.g. `builtin_interfaces/msg/Duration`); the type is named and the values are not used as a clock |
| `field_not_in_schema` | an indexed type on the path has no such field; nothing is used as a clock |
| `schema_unresolved` | the schema could not be indexed as far as the field, so its type is unknown and not verified |
| `payload_not_decoded` | the CDR payloads could not be decoded (e.g. decoder not installed, schema invalid), so the field type was not checked — this is never described as JSON or structure-only |
| `mixed_payloads` | the topic has both JSON and CDR messages; not verified |
| `structure_only` | JSON: there is no ROS type, so only that `<field>.sec` and `<field>.nanosec` are in-range integers was checked |

Anything other than `schema_verified` is also stated in a finding's `uncertainty`; the JSON limitation is also a standing statement. The field is omitted for expectations that do not use `header_stamp`.

## Clocks (rules, each tested)

* `event_response` is `mcap_log_time` only, for trigger and response. A contract that puts it on a payload clock (`time_source` other than `log_time`), selects a response clock, or carries a clock assertion is **invalid** (fails closed).
  Header stamps and payload fields in the recording are never read for it. Cross-payload-clock attestations are deferred to a later milestone.
* `log_time` is the recorder's **receipt time**; a stamp is the publisher's claim. Domains are never merged, shifted or converted; no offset is ever computed or guessed, and event occurrence is never inferred from receipt timestamps.
* Every event_response result is labelled as receipt time (finding `uncertainty`, rule parameters, and a standing statement).

## What a missing response means

The check is deterministic and reports a divergence when no matching response sample is in the recording by the deadline. The wording is limited to that: *no matching response was observed in the recorded stream by the deadline* (finding
discrepancy, e.g. "the trigger was received at … ns and no matching response was observed in the recorded stream by … ns (deadline … ns, receipt times)"). It does **not** say the robot never published the response. A silent or stopped publisher and a message that was published but not captured (dropped, not recorded, lost in transport or storage)
may be indistinguishable in a recording, so every such finding carries that caveat in `uncertainty`, and a standing statement says the same. As everywhere in this check, the times are recorder receipt order, not when events physically occurred.
A response that is in the recording but late is reported as such (`late_response`, "received at …") and carries no silence caveat. The response-state-unknown and first-trigger-entry safeguards are unchanged.

## Provenance

Every finding lists sources and observations with artifact ref, relative path, artifact sha256, topic, channel, message index (position in the file), timestamp, timestamp basis and clock domain (all `mcap_log_time` for event_response). New source roles: `trigger_event`, `late_response`. Blind spots carry the observation id and time of the message concerned.

## Performance (synthetic; this machine; do not extrapolate)

`python -m scripts.bench_m3b --events N --per-event M`. Two topics, whole Investigator run in a fresh process, `applies_when` with `max_age_ns` included; every observed event has a predecessor, a prior response sample and a response
(so all events resolve). "baseline" = a per-sample `equals` check on the same trigger stream. Re-run for revision 2 (Python 3.13.17, x86_64); no meaningful change from revision 1 (differences are within the run-to-run noise noted below; the revision-2 changes only touch reporting).

| Messages (2 topics) | Payload | baseline | event_response | Peak RSS (baseline / event_response) |
|---|---|---|---|---|
| 200,000 (999 events) | JSON | 2.5 s | 5.2 s | 140 / 257 MB |
| 200,000 | CDR (std_msgs/String) | 3.8 s | 5.9 s | 146 / 269 MB |
| 1,000,000 (4,999 events) | JSON | 12.8 s | 46.3 s | 543 / 1,121 MB |
| 1,000,000 | CDR | 16.6 s | 54.7 s | 567 / 1,177 MB |
| 2,000,000 (9 events) | JSON | 26.3 s | 44.6 s | 1,047 / 2,209 MB |
| 2,000,000 | CDR | 31.9 s | 57.2 s | 1,098 / 2,306 MB |

Plus the inventory step (about 0.7 s per 200k, 4–10 s per 1–2M messages). Timings vary between runs on this machine: the first (pre-revision) run of the 1M case took 31–41 s for event_response against 50–57 s now with essentially the same logic, so read the
time columns as ±40 %. Memory is stable: about 1.1 KB per message for event_response with a prerequisite. Time rose faster than linearly between 200k and 1M messages. 2,000,000 messages is the existing read guard;
beyond it the segment is reported incomplete. CDR rows use hand-written `std_msgs/String` messages; larger messages cost more to decode (M3a: Odometry about 14k msg/s).

## Limitations and known issues

* Trigger and response are single-signal conditions. No multi-trigger/AND, no repeated/windowed responses, no "response must hold for N ns", no minimum delay, no response-to-the-same-event matching when events overlap (each event is checked independently against the stream).
* The response is "a later matching sample", never shown to be caused by the trigger. A stream that already satisfied the response is undecided rather than counted.
* The deadline is covered by the recording's overall `log_time` coverage (first to last message across the selected segments). If the response topic has at least one earlier sample and none matches by the deadline, the finding says no matching response was *observed in the recorded stream*; it cannot tell publisher silence from capture loss (see above).
* Memory is the practical limit (see above); there is no streaming evaluation.
* The Phase 2 standing statement "Only json-encoded message payloads are decoded…" is now replaced only for runs in which CDR messages were actually decoded (JSON-only output stays byte-identical to Phase 2; a CDR run with the decoder unavailable keeps the original sentence, which is then true). This changes the text of M3a-era CDR investigation output, not Phase 2 output.
* A first trigger or response sample can make an otherwise clean run `insufficient_evidence`: if a recording starts with a trigger already true, or a response topic only starts after the trigger, those events are not resolved (by design, listed as blind spots). Start recordings before the behaviour of interest.
* The `mcap-ros2-support` licensing issue from M3a is unchanged and unresolved; it remains an optional, un-bundled extra.
* Real-recording validation (and `ros2idl` schemas, clock behaviour of real stamps) remains outstanding.

## Files

`investigator/contract.py` (EventResponseCheck, ResponseSpec, `max_age_ns`, `header_stamp`), `investigator/observations.py` (`header_stamp_ns`), `investigator/cdr.py` (`struct_type_at`), `investigator/engine.py`
(`_cond`, `_event_response`, staleness, results/roles), `tests/test_m3b_event_response.py`, `tests/make_m3b_example.py`, `examples/m3b/`, `scripts/bench_m3b.py`.
