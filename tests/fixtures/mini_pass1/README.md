# mini_pass1 fixture

Tiny, committable stand-in for a real pass-1 batch (50 cases instead of 25,000, so
every defect type in spec §4.2 appears at least once without checking a
25,000-line CSV into git).

Regenerate with:

```bash
python -m ledger_sense.data --seed 42 --pass-number 1 --n-cases 50 --out-dir tests/fixtures/mini_pass1
```

`tests/test_generator.py::test_committed_fixture_matches_documented_command` pins
this fixture against that exact command -- if the generator's output ever drifts,
that test fails and tells you to regenerate.
