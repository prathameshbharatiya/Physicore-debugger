# M3e requirement matrix

Maps each requirement of `M3E_PLAN.md` section 3 to the test families that exercise it. This shows that coverage exists for every requirement; it does not show that the coverage is sufficient (mutation results are in `docs/M3E_TEST_REPORT.md`).

| Requirement | Tests |
|---|---|
| FR-VW-01, 02, 03 (load, validate, digest) | test_ld, test_cli |
| FR-VW-04 (one view model for both formats) | test_vm, test_par |
| FR-VW-05 .. 16 (content of the view) | test_vm, test_md, test_ht, test_par, test_ex |
| FR-MD-01 .. 03 | test_md, test_par, test_adv |
| FR-HT-01 .. 06 | test_ht, test_js, test_adv, test_par |
| FR-JS-01 .. 08 | test_js (browser, Chromium only) |
| FR-VR-01 .. 03 | test_vr, test_cli |
| FR-SF-01 .. 04 | test_sf, test_adv, test_md, test_vr |
| FR-CLI-01, 02 | test_cli |
| NFR-VW-01 (determinism) | test_det |
| NFR-VW-02 (preservation) | test_pres |
| NFR-VW-03 (performance, reported) | test_scl, tools/bench.py (measured numbers in the test report) |
| NFR-VW-04 (resource) | test_scl, tools/bench.py |
| NFR-VW-05 (no new runtime dependency) | test_pres |

Files named test_* are in `tests/investigation_view/`; additional tests aimed at mutation survivors are in test_mut.
