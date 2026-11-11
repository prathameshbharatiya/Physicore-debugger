# M4b record: hypothesis catalogue and generation

Branch `m4-baseline`, built on M4a (81fc5b3). Local only; nothing pushed.

## What was built
- `rootcause/catalogue.py`: strict schema, loader (YAML/JSON, never raises), forbidden-wording screen on every sentence, closed vocabularies, digest, user extension entries (`user.` ids, always `user_supplied`, no review), merge.
- `rootcause/catalogue_v1.py`: the 12 built-in entries, every one `proposed`.
- `rootcause/instantiate.py`: investigation units (M3d components plus lone symptoms), deterministic matching, bindings with sources, instances, always-present members (two fixed M3d members, `unresolved`, `no_applicable_mechanism`). No ranking.
- `rootcause/review_pack.py`, generated `docs/M4_CATALOGUE_REVIEW.md`, guide `docs/M4_CATALOGUE.md`.
- Tests: `test_catalogue.py`, `test_catalogue_more.py`, `test_instantiate.py`, `test_review_pack.py`. Mutants: `tests/rootcause/tools/mutants_m4b.py`.
- No accepted test, CLI, or pinned deliverable was changed in M4b.

## Results actually run
- Mutation: 84 of 86 killed; 2 documented equivalents (NUL accepted; empty file) (`logs/m4b_mutation.log` is from the run before the last test addition; the one survivor of that run that was fixed, "ca: slots optional", was rerun alone and killed).
- Full suite, plain env: 3094 passed, 110 skipped, 1 failed: `SCL_07` (accepted M3d timing test, known intermittent; see M4A record). Log: `logs/m4b_plain.log`.
- Full suite, ros2 env with browser shim: 3277 passed, 7 skipped, 0 failed. Log: `logs/m4b_ros2.log`.
- Corpus coverage (dev scenarios): the true mechanism is in a set covering its topic for all 21 mechanism scenarios; no forbidden link; each family's required missing evidence appears in the true entry's evidence requirements. This shows the software follows its definitions, not accuracy on real robots.

## Open and not claimed
- All 12 entries are `proposed`. The user must review `docs/M4_CATALOGUE_REVIEW.md` and sign off (D12); nothing is claimed as reviewed.
- Evaluation of predictions and checks, the evidence ledger and ordinal tiers are M4c.
- SCL_07 intermittency: no clean plain full run on this tree.
- Real-recording validation and decoder licensing clearance remain outstanding. Delivery needs separate authorization.
