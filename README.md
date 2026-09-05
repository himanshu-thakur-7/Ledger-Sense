# Ledger Sense

> Finance software automates known rules. Ledger Sense learns the organization's recurring
> way of resolving the exceptions those rules can't handle.

Built for Syndicate by Maximor — Track 2, Autonomous Office of the CFO.

## Status

Repo skeleton (W0) + synthetic data generator (W1). No agent logic yet — see
`BOARD.md` for the build sequence and `Ledger-Sense-PRD.pdf` for the spec.

## Generator (spec §4)

Reference command — the exact `(seed, pass_number, n_cases)` every downstream
agent's acceptance targets are calibrated against:

```bash
python -m ledger_sense.data --seed 42 --pass-number 1 --n-cases 25000 --out-dir data/pass1
python -m ledger_sense.data --seed 42 --pass-number 2 --n-cases 25000 --out-dir data/pass2
```

This writes `ledger.csv`, `bank.csv`, and the ground-truth `match_links.csv` (never
read by Agents 1, 2, or 4 — law L2) into `data/pass1/` and `data/pass2/`, and prints
a run summary: row counts, the defect histogram, unique counterparty count, and the
overlay class + sibling count. `data/pass*/*.csv` is gitignored — regenerate rather
than commit; a tiny 50-case fixture lives at `tests/fixtures/mini_pass1/` for tests.

Add `--overlay` to enable the disclosed demo-overlay mode (BOARD.md locked Q3): it
plants 12–20 labeled `fee_offset` siblings for one counterparty, but only if no
naturally-occurring exception class already has 8+ siblings sharing the same
(counterparty, amount-delta bucket, reference pattern) shape. At the reference
dataset above (seed=42, pass 1), that natural check already finds a qualifying
class on its own (a single counterparty with 8 `wrong_reference` cases) — the
overlay correctly reports "not planted" there; this is the honest outcome (law
L15), not a bug. Every overlay row is labeled via the ordinary
`match_links.note` column (`overlay:fee_offset`), never a comment, so it stays
queryable.

## Layout

```
src/ledger_sense/
  data/       synthetic data model + generator (spec §4)
  matching/   Agent 1 — matching (spec §5)
  routing/    Agent 2 — ownership / routing (spec §6)
  guardrail/  Agent 4 — escalation / guardrail (spec §8)
  learning/   Agent 3 — resolution-learning (core bet)
  metrics/    Agent 5 — metrics orchestrator
tests/
```

Agents communicate only through files on disk — no agent package imports another agent's
internals (law L1 in `BOARD.md`).

## Install

```bash
pip install -e .
```

## Test

```bash
pytest
```

More to come as each agent lands.
