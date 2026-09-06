# Ledger Sense

> Finance software automates known rules. Ledger Sense learns the organization's recurring
> way of resolving the exceptions those rules can't handle.

Built for Syndicate by Maximor — Track 2, Autonomous Office of the CFO.

Every number in this document was printed by the commands shown, on this machine, on a clean
`pip install -e .` — seed `42`, `n-cases=25000`, both passes. Nothing here is copied from a PR
body or a spec target; where a measured number missed a target, that's said plainly instead of
rounded away.

## Why this exists

An unresolved exception doesn't just sit there — it becomes a human's problem, on a clock:
unresolved exception → human SLA queue → delayed close → stale forecast → CFO distrusts the
number. Straight-through rate (STR) is the one number in that chain that software can actually
shrink, and the only way to shrink it honestly is to stop re-litigating the same judgment call
every time it recurs. Ledger Sense does that for one narrow slice: a human resolves an exception
once, in structured fields; if they promote it, the resulting rule fires on the *next* batch,
before the exception ever reaches a queue.

This is **not "AI reconciliation."** There's no model doing free-form judgment calls on
transactions. The matcher is a deterministic, feature-weighted scorer (§5); the learned "rule" is
a predicate over that same feature vocabulary, promoted by an explicit human decision. See
[Limitations](#limitations) below for what this deliberately does not do.

## Install

```bash
pip install -e .
```

Installs three console scripts — `ledger-sense-generate`, `ledger_sense`
(`resolve`/`promote`/`apply-rules`), `ledger_sense-scoreboard` — plus `python -m
ledger_sense.matching` / `.routing` / `.guardrail`, which have no dedicated console-script entry
of their own. Run `pytest` — measured here: **277 passed, 2 skipped** (the 2 skips need a local
`MATCHING_BATCH_DIR`/real batch on disk, which a fresh clone doesn't have; harmless). The `slow`
marker (registered in `pyproject.toml`) covers the real-CLI end-to-end pipeline test — it runs by
default; `pytest -m "not slow"` excludes it if you want the fast subset only (275 pass, 2 skip).

## The whole pipeline, run for real

Every command below is the actual invocation used to produce the numbers in this README —
copy/paste them in order and they reproduce byte-identically (law L4: same `seed, pass_number,
n_cases` ⇒ same output, twice, on two different machines).

### Pass 1 — generate, match, route, guardrail

```bash
python -m ledger_sense.data --seed 42 --pass-number 1 --n-cases 25000 --out-dir data/pass1 --overlay

python -m ledger_sense.matching --ledger data/pass1/ledger.csv --bank data/pass1/bank.csv \
  --out-dir data/pass1

python -m ledger_sense.routing --outcomes data/pass1/match_outcomes.csv \
  --settlements data/pass1/ledger_settlements.csv --ledger data/pass1/ledger.csv \
  --bank data/pass1/bank.csv --as-of 2026-07-01T00:00:00Z --out-dir data/pass1

python -m ledger_sense.guardrail --ledger data/pass1/ledger.csv --bank data/pass1/bank.csv \
  --outcomes data/pass1/match_outcomes.csv --settlements data/pass1/ledger_settlements.csv \
  --as-of 2026-07-01T00:00:00Z --period-start 2025-12-01T00:00:00Z \
  --period-end 2026-07-01T00:00:00Z --out-dir data/pass1
```

`--period-start`/`--period-end` pin the `out_of_period` window to this batch's actual
`value_date` spread. Omit them and guardrail defaults to a single calendar month around
`--as-of`, which — against this generator's ~6-month spread — holds the vast majority of lines
by design (documented, CLI-overridable; not a bug). The measured split below uses the explicit
full-batch window.

**Measured, this run:** 24,500 ledger rows / 27,250 bank rows / 800 unique counterparties.
Cheap-tier match rate **83.93%** (22,872/27,250). Routing opens **4,088 exceptions** (duplicate
1,250, suspect_posting 1,316, timing 758, amount_mismatch 500, unidentified_counterpart 264).
Guardrail: **allow 90.58%, block 8.72%, hold 0.69%** (24,684 / 2,377 / 189 of 27,250).

### A human resolves one exception — structured fields, not "approve"

```bash
ledger_sense resolve \
  --exceptions data/pass1/exceptions.csv --outcomes data/pass1/match_outcomes.csv \
  --exception-id EXC-PAIR-BK-P1-002463-LG-P1-002214 \
  --resolution-type reference_transform \
  --reference-transform wrong --amount-class exact \
  --rationale "AR ops always trusts an exact amount match even when the bank quotes a different reference string -- recurring org behavior, not a one-off" \
  --resolved-by himanshu --resolved-at 2026-07-01T00:00:00Z \
  --candidates data/candidates.json
```

Printed, verbatim:

```
resolution_id=RES-39f220941fae9aa9
exception_id=EXC-PAIR-BK-P1-002463-LG-P1-002214
resolution_type=reference_transform
rule_id=RULE-f5587af7405c
candidate predicate: amount_class=exact AND reference=wrong
support count against current exception pile: 87
status=candidate
```

`--resolution-type`, `--reference-transform`, `--amount-class` etc. are enum/feature-space fields
in the matcher's own vocabulary (law L11) — there is no free-text "approve" path. The predicate
is deliberately *not* scoped to one counterparty: it says "the org accepts an exact-amount match
regardless of a mismatched reference," which is exactly the kind of recurring policy a human
actually applies across many counterparties, not a memoized transaction. Support count (87) is
computed live against the current `exceptions.csv`/`match_outcomes.csv` on disk, not asserted.

### Promote — explicit "yes, always" is the only path that writes `rules.json`

```bash
ledger_sense promote RULE-f5587af7405c --confirm yes-always \
  --promoted-by himanshu --promoted-at 2026-07-01T00:05:00Z \
  --rules data/rules.json --candidates data/candidates.json
```

```
RULE-f5587af7405c <- RES-39f220941fae9aa9
```

### Pass 2 — a genuinely new draw, rules applied before routing sees it

```bash
python -m ledger_sense.data --seed 42 --pass-number 2 --n-cases 25000 --out-dir data/pass2 --overlay

python -m ledger_sense.matching --ledger data/pass2/ledger.csv --bank data/pass2/bank.csv \
  --out-dir data/pass2

ledger_sense apply-rules \
  --outcomes data/pass2/match_outcomes.csv --settlements data/pass2/ledger_settlements.csv \
  --ledger data/pass2/ledger.csv --bank data/pass2/bank.csv --rules data/rules.json \
  --as-of 2026-07-01T00:00:00Z --period-start 2025-12-01T00:00:00Z \
  --period-end 2026-07-01T00:00:00Z --out-dir data/pass2

python -m ledger_sense.routing --outcomes data/pass2/match_outcomes.csv \
  --settlements data/pass2/ledger_settlements.csv --ledger data/pass2/ledger.csv \
  --bank data/pass2/bank.csv --as-of 2026-07-01T00:00:00Z --out-dir data/pass2

python -m ledger_sense.guardrail --ledger data/pass2/ledger.csv --bank data/pass2/bank.csv \
  --outcomes data/pass2/match_outcomes.csv --settlements data/pass2/ledger_settlements.csv \
  --as-of 2026-07-01T00:00:00Z --period-start 2025-12-01T00:00:00Z \
  --period-end 2026-07-01T00:00:00Z --out-dir data/pass2
```

`apply-rules` runs strictly between Agent 1's own matching CLI and Agent 2's routing — it checks
every line that *would have* escalated against `rules.json`, re-derives Agent 4's veto
independently (`would_block_or_hold`, law L12) before firing, and only then hands the residual to
routing. Printed, verbatim:

```
rules loaded: 1
escalated lines seen: 1652
escalated lines matching a rule's predicate: 75
vetoed by guardrail (would_block_or_hold != allow): 0
predicate hit but no ledger capacity remained: 0
resolved by rule: 75
  RULE-f5587af7405c: 75 lines resolved
```

**Measured, this run:** 24,513 ledger rows / 27,263 bank rows (a fresh, non-overlapping draw —
same 800 counterparties, same defect mix, not an easier batch). Cheap-tier match rate **83.91%**
(22,876/27,263) — within 0.02pp of pass 1's, i.e. no easier a batch (law L6).
Routing opens **4,039 exceptions** (75 fewer than would otherwise have escalated, resolved by the
rule before routing ever saw them). Guardrail: **allow 90.65%, block 8.68%, hold 0.66%**.

### Scoreboard — the side-by-side, computed only from files on disk

```bash
ledger_sense-scoreboard scoreboard --pass1-dir data/pass1 --pass2-dir data/pass2 \
  --rules data/rules.json --out data/scoreboard.json
```

The scoreboard binary is its own script, `ledger_sense-scoreboard scoreboard` — **not** bare
`ledger_sense scoreboard` — because a second entry under the `ledger_sense` key would collide
with `resolve`/`promote`/`apply-rules`.

**Measured, this run (the real numbers, not targets):**

| | Pass 1 | Pass 2 |
|---|---:|---:|
| STR (naive, matched+settled) | 87.51% (23,846/27,250) | 87.65% (23,897/27,263) |
| STR (real, vs ground truth) | 87.51% (23,846/27,250) | 87.65% (23,895/27,263) |
| Match precision vs ground truth | 100.00% (24,336/24,336) | 99.99% (24,432/24,435) |
| Exceptions remaining | 4,088 | 4,039 |
| Rule-driven auto-resolves | — | 75 |
| Trace coverage (rule → resolution) | — | 100.00% |
| Learned rules | — | 1 |

Ground truth (`match_links.csv`) is read here — and only here (law L2) — because Agent 5 is the
one agent explicitly permitted to grade against it.

**Exception-class elimination, honestly split, not inflated into one headline number:** 924
exception classes (`counterparty | amount-bucket | reference-pattern`) present in pass 1
disappear entirely in pass 2. Of those, **84** share the promoted rule's exact shape (amount
bucket `0`, reference pattern `wrong`) — the number directly attributable to the rule. The
remaining **840** are ordinary two-independent-draws variance (a class with a handful of
siblings in pass 1 simply didn't recur in pass 2's fresh draw) and are *not* claimed as a
learning result. Every one of the 75 rule-driven resolves in the trace table carries both
`rule_id=RULE-f5587af7405c` and `resolution_id=RES-39f220941fae9aa9` at 100% coverage — 0 vetoed,
0 memoized transaction ids (law L1/L12 hold live, not just in a unit test).

## Sponsor disclosure

This build ran with **no Docker** and **no `TENSORMUX_*` / `NEATLOGS_*` / `DODO_*`** environment
variables set. The cheap-tier matcher (§5) is the only path actually measured above — every
number in this README came from it. The expensive/LLM adjudication tier is a documented stub
seam only: `llm_is_stub=True`, `llm_calls=0` on every run (law L9); no LLM was called, no
container was started, and no sponsor infrastructure was required to produce any number in this
document. Neatlogs and Dodo Payments are out of this build entirely — no integration, stub or
otherwise, was attempted.

## Overlay disclosure

The generator can plant a labeled demo overlay so a class-elimination moment is guaranteed
visible in one run, but it only does so when the batch doesn't already have one naturally. In
**this actual run**, at `seed=42`:

- **Pass 1:** the overlay did **not** fire. The generator's own natural-cluster check found a
  real (non-planted) class of exactly 8 siblings — one counterparty's `wrong_reference` cases —
  which already met the 8-sibling gate, so the printed summary reports `not planted (natural
  cluster already qualified)`. Zero overlay rows exist in pass 1.
- **Pass 2:** the same fresh draw's natural max cluster was only 7 (below the gate), so the
  overlay **did** plant 13 labeled `fee_offset` siblings, each one tagged
  `overlay:fee_offset` in `match_links.csv`'s `note` column — auditable, not indistinguishable
  from organic data.

Restated in the disclosed form the spec requires, with "Pass-1" honestly replaced by the pass
where the overlay actually fired this run: *Pass-2 includes a labeled overlay of 13 sibling
fee-offset exceptions so the learning moment is visible in one run; matching and resolution logic
are not scripted.* Pass 1's own overlay was not needed — the batch already had a naturally
occurring class at the gate. The rule promoted and measured above was learned from a
**naturally occurring** class, not the overlay; the overlay siblings are separate, still-unresolved
rows in this particular run, since no promoted rule targets `fee_offset`.

## How AO was used

One orchestrator session coordinated 8 worker sessions across cards W0–W7, each on its own
branch and its own git worktree (two of the eight, mid-project, had a dead first attempt with
zero pushed work — replaced by a fresh session on a new worktree rather than resumed). W0–W6
shipped as PRs #1–#7, each squash-merged into `main`; this document ships as part of W7 (last
card). There was no GitHub review ritual on any of them — the human explicitly authorized the
orchestrator to merge directly once a card's own acceptance tests were green and its file-scope
(the "Writes / may touch" list in `BOARD.md`) was independently verified, rather than waiting on
a separate reviewer pass for every card.

## Layout

```
src/ledger_sense/
  data/       synthetic data model + generator (spec §4)
  matching/   Agent 1 — matching (spec §5, own README with scoring detail)
  routing/    Agent 2 — ownership / routing (spec §6)
  guardrail/  Agent 4 — escalation / guardrail (spec §8)
  learning/   Agent 3 — resolution-learning (core bet, spec §7)
  metrics/    Agent 5 — metrics orchestrator (spec §9)
tests/
```

Agents communicate only through files on disk — no agent package imports another agent's
internals (law L1). `data/pass*/*.csv` is gitignored; regenerate with the commands above rather
than committing 25k-line CSVs. A tiny 50-case fixture lives at `tests/fixtures/mini_pass1/` for
fast unit tests.

## Limitations

- **Near-relative decoy names can slip through the matcher.** An exact reference match against a
  fuzzy-but-wrong counterparty name (measured here: `name_similarity("Alpha Systems Group",
  "Alpha Systems Trading Co")` = 0.8125) can outscore the true candidate through
  `PARTIAL_WITH_EXACT_REFERENCE` (spec §5.4). This is documented and regression-pinned —
  `tests/test_matching.py::test_known_near_relative_partial_decoy_limitation` — not "fixed" with
  a full-ledger scan, which would change the matcher's blocking design (no full-scan fallback is
  a deliberate constraint, not an oversight).
- **Agent 2 assigns a named person from a fixed roster via blake2b(counterparty). It does not discover the real organizational owner of a dependency. Routing exists to feed learning and SLA, not to replace org design.**
- **The LLM/expensive adjudication tier is a stub, not a working integration** (see Sponsor
  disclosure above). `llm_is_stub=True` and `llm_calls` is measured on every run specifically so
  this can never be silently claimed otherwise.
- **The learned rule promoted above is unscoped by counterparty on purpose**, which means its
  84-class attribution spans many different counterparties sharing one shape. This is the
  intended generalization (law L11: a predicate over the matcher's feature space, not a
  memoized transaction), but it also means one human decision can suppress escalations across
  the whole counterparty universe at once — worth knowing before promoting a rule for real.

See `Ledger-Sense-PRD.pdf` for the full spec and `BOARD.md` for the build sequence, standing
engineering laws, and per-card acceptance detail.
