# M3d example — `evidence localize`

A tiny **synthetic** recording (`example_bag/`): an ERROR log message at +1 ms on `/rosout` and a diagnostic ERROR at +3 ms on `/diagnostics`.
`dependencies.example.yaml` declares one edge `/rosout -> /diagnostics` with a 10 ms window. Nothing here is real-robot data.

```
physicore-collector evidence localize examples/localization/example_bag \
    --anomaly-config examples/localization/anomaly.example.yaml \
    --dependencies  examples/localization/dependencies.example.yaml          # readable
(add --json for the machine-readable report; omit --dependencies for an order-only report)
```
`expected_output.txt` and `expected_output.json` are the exact outputs of this command (exit code 0). They are regenerated, never edited by hand.
Reading it: the earliest symptom in the log-time lane is the log message; the link label is `consistent_with_propagation` (the receipt order fits the declared edge),
which is **not** a statement that the first symptom caused the second, and not a statement about which event occurred first in the robot.
