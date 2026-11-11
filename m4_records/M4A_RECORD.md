# M4a record: the declared system model

Branch `m4-baseline`, local only. Builds on M4-0. No push, no remote change. No delivery package has been made (it needs separate authorization).

## What was built

| Item | Content |
|---|---|
| `src/physicore_collector/rootcause/model.py` | schema 1 loader: components, bindings, versions, four constraint kinds, interventions, edges (M3d syntax), operating context; every item records who declared it; strict, bounded, never raises; reuses the accepted dependency loader's hardened YAML/JSON scanning unchanged |
| `src/physicore_collector/rootcause/modelcheck.py` | recording facts from the inventory (kept apart from declared facts) and seven fixed-wording finding kinds; JSON and text report |
| `cli.py` | one added command, `evidence system-model MODEL [--evidence] [--dependencies] [--json]`, imported lazily; exit codes 0, 1, 2, 3 |
| `docs/M4_SYSTEM_MODEL.md`, `examples/rootcause/` | guide; example model with its recorded text and JSON output (checked byte for byte) |
| Tests | `test_model.py` (MOD), `test_modelcheck.py` (CHK), `test_cli_model.py` (CLIM), `test_example_model.py` (EXM): 138 new tests |

## Edits to accepted tests (additive or narrow, each pinned by `tests/rootcause/test_pres.py`)

Adding a command changes the group help and the CLI diff, so two accepted checks needed to know about it:

| File | Edit |
|---|---|
| `tests/localization/test_preservation.py` | 8 lines added after the help-golden loop: the `system-model` row is removed too before comparing with the accepted golden |
| `tests/investigation_view/test_pres.py` | one line widened: the M3e CLI-diff check reads the CLI without the M4 command |

`cli.py` differs from the M3e commit by one added command and nothing else (PRES_06).

## Results

| Environment | Result | Log |
|---|---|---|
| ros2 (with browser shim) | 3212 passed, 7 skipped, 0 failed | `logs/m4a_ros2.log` |
| plain | 3029 passed, 110 skipped, **1 failed** | `logs/m4a_plain.log` |

The one failure is the accepted M3d timing test `test_SCL_07_all_symptoms_overlapping_layering_is_near_linear` (it compares the time of 50k and 100k intervals). It passes when run alone and is intermittent in full runs: on the M4a code, plain full runs gave 1 clean run of 5 and ros2 full runs gave 1 failure of 4. It also fails at the M3e commit when the `tests/localization` directory runs on its own, so it is not new, but it failed more often in M4a runs than in the earlier ones and I could not find out why. A mitigation I tried (pausing garbage collection during that test) did not stop it, and was removed. I did not get a clean plain full run on the final tree. The M3d test and its bound are untouched.

Mutation (`tests/rootcause/tools/mutants_m4a.py`, 117 mutants of `model.py`, `modelcheck.py` and the command): **112 killed, 5 survived**, in one complete run on the final code (`logs/m4a_mutation.log`). The first run left 22 survivors; 17 tests were added for real gaps (duplicate keys and YAML features inside an otherwise valid file, invalid UTF-8 inside a valid file, exact size limit, ordering inside a kind, text and message details). One redundant boolean check was removed from the source. The 5 survivors are no-ops by construction and are explained in the tool's docstring: an item kept despite a problem is never returned (2), the content digest sorts keys that are already in fixed order, NUL characters are refused by the parsers or the control-character check, and the early file-size check only avoids opening a file whose read is capped anyway.

## Findings

1. A YAML alias always follows an anchor, and the anchor is refused first, so the "aliases" message of the accepted scanner is unreachable in practice.
2. A directory with no recording in it is the case where the inventory fails (exit 2); a damaged or truncated recording still gives a report with exit 1.
3. The rate and value constraints are only checked here for whether they can be evaluated; evaluation belongs to later steps.

## Not done, not claimed

- Real-recording validation (M3a) and decoder licensing clearance remain outstanding.
- The model is declared by people and cannot be verified; the tool does not say whether the model or the recording is right.
- Constraints are not evaluated yet; the `deadline_ns` and `value_range` data are loaded but unused until M4b/M4c.
- No accuracy claim of any kind.
