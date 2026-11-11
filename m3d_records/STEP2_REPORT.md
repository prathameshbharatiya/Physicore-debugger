# M3d Step 2 report (actual results)
Files: `localization/symptoms.py` (+ vocabulary additions `payload_stamp_value`, `NORMALISER_VERSION`); tests `tests/localization/{l2.py,test_sym.py}`; `tools/mutants_step2.py`; `STEP2_ONSET_TABLE_VERIFICATION.md`.
Tests executed: test_sym.py 37 (ros2 venv) / 36 passed + 1 skipped (plain venv; the skip names its reason: the M3b example recording is CDR). SYM-01..21 plus 10 strengthening tests.
Full suites: **ros2 1880 passed, 6 skipped, 1 failed; plain 1778 passed, 108 skipped, 1 failed.** The single failure in each is the same open G1 conflict recorded in Step 1 (accepted M3c test PRES_09 `...before_step_9`); no other failure.
Mutation (MUT-SYM): 51 mutants; first run 39 killed / 12 survived; after 10 strengthening tests, **51 killed, 0 survived** (run in the ros2 venv; the survivors were test gaps, none equivalent).
Onset-table verification: see `STEP2_ONSET_TABLE_VERIFICATION.md` (no row failed; three refinements for review).
Not claimed: end-to-end CLI, blockers, links, chains (later steps).
