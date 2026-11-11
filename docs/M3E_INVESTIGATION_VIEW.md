# M3e: Investigation view and evidence-backed reports

`evidence render` turns a PhysiCore localization report (the JSON written by `evidence localize --json`, schema "1.0") into either a Markdown report or one
self-contained, offline HTML page. It is a **presentation layer only**: it performs no analysis, reads no other input, and every statement it prints is
either a fixed sentence of the report schema, one of its own fixed templates, or cleaned and escaped data from the report. No generated sentence states why anything happened.

## Usage

```
physicore-collector evidence render REPORT.json --format markdown > report.md
physicore-collector evidence render REPORT.json --format html > report.html
physicore-collector evidence render REPORT.json --format html --verify-evidence PATH_TO_RECORDING > report.html
```

Output goes to standard output as UTF-8 bytes; nothing is written to disk and the report and recording are opened read-only. Problems are printed to standard error.
Standard input (`-`) is not accepted.

| Exit code | Meaning |
|---|---|
| 0 | rendered |
| 1 | rendered, but `--verify-evidence` found a mismatch |
| 2 | the report could not be read, or a usage error |
| 3 | the report is invalid, of an unsupported schema, or larger than 64 MiB (nothing is rendered) |

## What the view contains

Header and provenance (report SHA-256 and size, tool version, exit code and reasons); first divergence; one section per clock lane with its own time axis (lanes are
never compared with each other); symptoms; links; chains; components; localization hypotheses; blockers and caveats (never capped); coverage and the component
diagram; limits and limitations; and an evidence-reference table (E1, E2, ...) that every symptom and blocker cites.

Times are exact integer nanoseconds; offsets are printed as `+ms.nnnnnn ms` with integer arithmetic only. Tables list at most 200 rows and cells at most 20 items;
every omission is counted where it is cut, and the JSON report remains the complete record.

## The HTML page

One file, no network access, no external reference, no third-party code. A strict Content-Security-Policy (hashes for the one script and one style block,
`default-src 'none'`, `connect-src 'none'`) is embedded. The static layer is complete without JavaScript. With JavaScript the page adds filters, 100-row pagination,
selection and highlighting across tables and timelines, per-lane zoom and pan (exact BigInt arithmetic), a copy button and fragment links (`#s<index>`).
Tested in Chromium only; accessibility checks are made (names, roles, keyboard reach, contrast of the fixed palette) but no WCAG conformance is claimed.

## `--verify-evidence`

Re-reads the recording at the given path (a directory, or a single file whose name matches the reference) and marks each reference `verified`, `mismatch` or
`not_checked`: the file SHA-256, then the message at the recorded file-order position, its topic, and, for the log-time clock domain only, its log time.
References that leave the evidence location (absolute paths, `..`, links out of the directory, wrong scheme) are `not_checked`. It reports only agreement or
disagreement with the recording on this run.

## Limits and honest scope

- Only schema "1.0" reports are accepted; the report is validated strictly (no BOM, no duplicate keys, no floats or NaN).
- Real-recording validation and decoder licensing clearance remain outstanding; M3e was exercised on the bundled example and generated reports.
- To satisfy the accepted M3c preservation test (which forbids one word in non-exempt modules) the detector-configuration digest is read generically; the page
  says "detector configuration".
- Not claimed: any statement of why a fault happened, WCAG conformance, support for browsers other than Chromium.
