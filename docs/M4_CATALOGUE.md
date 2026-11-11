# M4b: the hypothesis catalogue

The catalogue is a closed list of candidate mechanisms (twelve in version 1). A hypothesis is never free text: it is one catalogue entry bound to real
topics of one investigation unit. Nothing is ranked or scored at this stage, and nothing here says that any mechanism is the cause.

## Investigation units
A unit is one M3d component, or one symptom that belongs to no component. Each unit gets its own competing set.

## What a competing set holds
- one instance for every entry that applies to the unit (its symptom categories match, and its component roles match when a system model is given);
- the two fixed M3d members (a common or independent source is not excluded; an artifact of observation or timing is not excluded);
- an explicit `unresolved` member, always;
- a `no_applicable_mechanism` member when no entry applies.

## Bindings
Each entry names slots (for example `silent_topics`). A slot is bound from the M3d report (symptom topics), from the system model (siblings, role bindings,
declared edges) or from a dependency file's declared edges. Every binding records its source. A slot that cannot be bound stays empty, and a check that needs
it is reported as `not runnable: binding missing`. Nothing is guessed.

## Entries
Each entry carries one neutral mechanism sentence, the symptom categories it applies to, predictions with a rule, optional checks (M2 kinds), the evidence
that would help separate it from its rivals, and its rivals. All sentences are screened against the forbidden-wording list of M3d when the file is loaded.

## Review
All twelve v1 entries are `proposed`. An entry becomes `reviewed` only when a named reviewer and a date are written into the catalogue itself. The generated
review pack, `docs/M4_CATALOGUE_REVIEW.md`, lists every entry for that purpose. This tool never marks an entry as reviewed.

## User extension entries
A user file in the same schema may add entries. Their ids start with `user.`, they cannot carry a review, and they are always shown as `user_supplied`.

## Limits
Real-recording validation and decoder licensing clearance are outstanding. Instances are candidates only; their evaluation is M4c.
