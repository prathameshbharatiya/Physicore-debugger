# M3d — Clock-safe incident timeline foundation

This module adds a canonical, evidence-linked event representation for building an incident timeline.

- Event classes are explicit: sensor, controller, actuator, transport, recorder, operator, anomaly, transition, and divergence.
- Every event carries a source recording, artifact digest, stable source reference, and optional topic/message index.
- Timestamped events must declare a clock domain. Events without a usable timestamp remain unplaced.
- Observed and inferred timestamps are distinct fields; inferred time is never presented as observed.
- Events are sorted only within a single clock domain. No offset, synchronization, or cross-domain ordering is invented.
- Context windows are extracted only within the selected clock group.

`investigator.timeline` is an additive timeline data layer. It does not yet synthesize operator events or infer semantic event classes from arbitrary topic names. Callers must supply a grounded event class and source reference. Integration with the CLI/report workspace and a synchronized telemetry viewer remains subsequent work.

The current first-divergence engine remains authoritative for expectation-based divergence. The timeline layer is a presentation and aggregation primitive, not a replacement for that engine and not a causal diagnosis.
