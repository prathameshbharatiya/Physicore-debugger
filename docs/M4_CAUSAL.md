# M4d: the claim ladder and the causal layer

Every hypothesis instance gets exactly one claim level, 0 to 4. The words that may be used about it come from a fixed table keyed by level, and a gate
refuses a stronger word than the level allows. Level 5 is not reachable here, and this tool never writes "caused".

| Level | Name | Evidence required | Fixed wording |
|---|---|---|---|
| 0 | observed order | symptoms observed in the recording (M3d) | "The symptoms of this unit were observed in the recording in the order listed." |
| 1 | consistent with a declared dependency | a supporting symptom reached through a consistent M3d link | "...consistent with propagation along the declared edge." |
| 2 | compatible with a mechanism | the instance is supported, at least half of its direct predictions are met, none is unmet or contradicted | "The evidence is compatible with this mechanism; it is supported by the listed evidence." |
| 3 | alternatives excluded within coverage | level 2, and every listed rival is present in the set and directly contradicted | "The recorded evidence favours this mechanism over the listed alternatives." |
| 4 | difference evidence | level 3, and a contrast: a comparable baseline lacking the supporting symptoms, or a declared intervention before all of them | "The evidence indicates this mechanism, supported by a contrast between these recordings." |
| 5 | controlled intervention | a deliberate test on the system | not reachable; belongs to M5 |

The level is the highest whose evidence is fully present. Missing evidence never raises it. **Caps:** a blocker in the unit's component, a check that could not
be run or answered, or a direct contradiction holds the level at 1 and says why. A baseline that is not comparable (different detector configuration, dependency
file, or parameters) removes level 4 and names what differs.

Each hypothesis states what would raise it to the next level. For sets that have no leader, diagnostic asks list the measurements that would
discriminate, the alternatives they separate from, and what each outcome would mean. Nothing here executes a test.

**Propagation facts.** For every declared edge that touches a unit, the report cites the M3d edge coverage and states whether the upstream endpoint was covered and
symptom-free, whether both ends show symptoms, whether the upstream end was not covered (so its silence says nothing), or whether the downstream end shows
nothing. These facts are reported next to the unit; in v1 they do not change a tier, because no v1 entry is keyed to an upstream origin.

**Wording table.** The table above is a proposal and needs your sign-off. The word "propagation" is released only from level 1; every other word that the
localization step forbids stays forbidden at every level.

## Limits
Results on the synthetic corpus show the software follows its definitions, not accuracy on real robots. On the development corpus only levels 0 and 2 occur.
Real-recording validation and decoder licensing clearance are outstanding.
