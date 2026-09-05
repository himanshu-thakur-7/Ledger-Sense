# Ledger Sense

> Finance software automates known rules. Ledger Sense learns the organization's recurring
> way of resolving the exceptions those rules can't handle.

Built for Syndicate by Maximor — Track 2, Autonomous Office of the CFO.

## Status

Repo skeleton (W0). No agent logic yet — see `BOARD.md` for the build sequence and
`Ledger-Sense-PRD.pdf` for the spec.

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
