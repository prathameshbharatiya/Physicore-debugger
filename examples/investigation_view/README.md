# Investigation view example

`report.json` is the M3d example localization report. The other files are the output of `evidence render`, regenerated (never hand-edited), from the repository root, by:

```
physicore-collector evidence render examples/investigation_view/report.json --format markdown > examples/investigation_view/report.md
physicore-collector evidence render examples/investigation_view/report.json --format html > examples/investigation_view/report.html
physicore-collector evidence render examples/investigation_view/report.json --format markdown --verify-evidence examples/localization/example_bag > examples/investigation_view/report_verified.md
```

`tests/investigation_view/test_ex.py` re-renders each file and fails if it differs. Real-recording validation remains outstanding.
