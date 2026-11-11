# M4e: the hypothesis report and `evidence hypotheses`

```
physicore evidence hypotheses REPORT [--format json|markdown|html] [--model FILE] [--dependencies FILE] [--evidence PATH] [--baseline REPORT]
                                      [--catalogue FILE] [--verify]
```
(The plan called this command `evidence investigate`; that name already belongs to the accepted M2 command, so the new command is `evidence hypotheses`.)

REPORT is a localization report from `evidence localize --json`. The command is read-only and writes only to standard output.

| Option | Use |
|---|---|
| `--model` | A system model (M4a). Binds siblings, role slots and declared edges; its digest is shown. Declared by its authors, not verified. |
| `--dependencies` | The dependency file the report was made with. Its digest must equal the one in the report, or the run refuses (exit 3). Its edges bind declared neighbours. |
| `--evidence` | The recording. Needed to run generated range checks (only if its digests equal the report's) and for `--verify`. |
| `--baseline` | A second localization report, for level-4 contrast. Comparability (detector configuration, dependency file, parameters) is checked and any difference is named. |
| `--catalogue` | Extra entries in the catalogue schema; they are marked `user_supplied`. |
| `--verify` | Re-reads the recording and checks every evidence digest and message reference the report cites. |

**Exit codes:** 0 done; 1 done, but a verification found a mismatch or the recording did not match the report; 2 an input could not be read, or a usage error;
3 an input is invalid or does not fit the others (nothing is produced).

**The JSON report** (`investigation_report_version` 1.0) is strictly typed: unknown or missing keys, a claim level outside 0 to 4, a sentence that is not the fixed
sentence of its level, a contradicted hypothesis above level 1, a leader that is not supported, or a key that uses a word this tool never emits all make the
report invalid, and the run refuses to print it.

**Markdown and HTML** are rendered from one list of blocks, so they cannot disagree. The page has no script and loads nothing external. All data text is
cleaned and escaped. An independent checker, `tests/rootcause/tools/parity_m4.py`, which imports nothing from the product, compares the three outputs.

**Reading it.** A tier orders candidates; it is not a probability. A claim level is the highest level whose evidence is fully present (see M4_CAUSAL.md).
`unresolved` and `competing` are legitimate results. Every catalogue entry is shown with its review status; until a named reviewer signs an entry off it is
`proposed`.

## Not in M4e
The M3e timeline components are not reused: evidence references are listed as text and verified against the recording instead of drawn. The delivery package
(zip, patch, checksums) is not built; it needs separate authorization.

## Limits
Real-recording validation and decoder licensing clearance are outstanding. Results on the synthetic corpus show the software follows its definitions, not
accuracy on real robots.
