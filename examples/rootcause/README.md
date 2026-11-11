# Example: a declared system model (M4a)

`system_model.example.yaml` is an invented model for the bag in `examples/localization/example_bag`. Run from the repository root:

```
python -m physicore_collector evidence system-model examples/rootcause/system_model.example.yaml --evidence examples/localization/example_bag
```

The readable output is `system_model_report.txt`; with `--json` it is `system_model_report.json`. Both are checked byte for byte by the tests.

What it shows: the model is declared, the recording facts are listed separately, and four differences are reported as findings (the lidar topic is not in
the recording, the two constraints on it cannot be evaluated either, and the declared restart lies outside the recording's time window). The tool does
not say which side is right and changes neither.

Nothing in this example is a real robot or a real recording.

## Hypothesis report (M4e)

`hypotheses_report.json`, `.md` and `.html` were produced from `examples/localization/expected_output.json` with
`physicore evidence hypotheses examples/localization/expected_output.json --format json|markdown|html`. No system model, dependency file or recording was
given, so declared neighbours are not bound and the generated checks were not run; the report says so. All catalogue entries are `proposed`. A test
regenerates the three files and fails if they differ.
