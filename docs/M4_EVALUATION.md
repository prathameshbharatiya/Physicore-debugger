# M4c: evaluating hypotheses

Each hypothesis instance (M4b) is held against the M3d report and the outcomes of its checks. The result has, per investigation unit, an ordered list of
instances with an evidence ledger, a tier, and a verdict on the set. Nothing here is a probability, and no mechanism is called the cause.

## Evidence ledger
Every item is one of four kinds, and has a stance towards the instance:
- `observed`: a symptom in the M3d report (with its evidence references), or a check answered by an M3d symptom;
- `inferred`: produced by a named rule from inputs (an M3d link label; a generated check);
- `assumption`: a binding taken from the system model or a dependency file, which this tool does not verify;
- `unconfirmed`: a measurement that would help and is usually absent (named, never invented).
Stance: `supports`, `contradicts`, `neutral`, `missing`.

## How a prediction is judged
- A prediction about a declared neighbour (upstream or downstream topic) counts only through the M3d link between that symptom and a symptom of the unit:
  `consistent_with_propagation` satisfies it; `inconsistent_order` or `outside_declared_window` contradicts it; `undetermined` or no link leaves it open.
- A prediction about the unit's own symptom topics only restates the symptom that made the entry match. It is neutral.
- A symptom that is not reported is never proof that nothing happened (the report does not say which topics a detector covered). "Absent" predictions can
  therefore only be contradicted, never satisfied.
- A check is answered by an M3d symptom of the matching M2 kind, or by a generated M2 contract (value ranges compiled from declared `value_range`
  constraints, hashed, run only when the recording's digests equal the report's). Other cases are `not_runnable` or `not_run` with a reason.

## Tiers and the leader rule
Tiers: `contradicted`, `not_assessed`, `supported`, `possible`. Instances are ordered by: no contradiction, larger satisfied share, fewer open predictions,
entry id. A set has a **leader** only if the first instance is supported, its satisfied share is at least 1/2, it exceeds every other non-contradicted
instance by at least 1/3, and the unit has no blocker. Otherwise the set is `competing`, or `unresolved` if nothing is supported. `unresolved` is a
legitimate result.

## Assumptions and sensitivity
Every binding taken from the model or a dependency file is listed. The whole evaluation is repeated without each source, and the result says whether the
verdicts changed.

## Not built in M4c
- Plausibility against a declared rate constraint: no v1 entry makes a rate prediction, so nothing could use it.
- Check windows from chain intervals: generated range checks run over the whole recording.
- The claim ladder and any causal wording (M4d); the report and page (M4e).

## Limits
Results on the synthetic corpus show that the software follows its definitions, not accuracy on real robots. Real-recording validation and decoder
licensing clearance are outstanding.
