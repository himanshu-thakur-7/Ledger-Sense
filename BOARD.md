# Ledger Sense — Build Board

Syndicate by Maximor — Track 2, Autonomous Office of the CFO. ~30h, remote solo.
Source of truth: `Ledger-Sense-PRD.pdf` (repo root). If any card here ever disagrees with the
PDF on a schema, threshold, or filename, STOP and escalate to the human — do not invent a
third version.

## Goal + demo contract

Finance software automates known rules. Ledger Sense learns the organization's recurring way
of resolving the exceptions those rules can't handle.

Five file-spine agents + one deterministic synthetic generator:

| # | Agent | Writes |
|---|-------|--------|
| 1 | Matching | `match_outcomes.csv`, `ledger_settlements.csv` |
| 2 | Routing | `exceptions.csv`, `owner_queues.csv` |
| 3 | Learning (core bet) | `rules.json` |
| 4 | Guardrail | `release_decisions.csv`, `guardrail_audit.csv` |
| 5 | Metrics | scoreboard, computed from two real passes |

Demo must be true, not claimed:
1. Pass 1 runs cold on `seed=42` (25,000 cases; 26,738 bank lines is the calibrated reference shape — PDF's exact counts govern).
2. A human files a STRUCTURED resolution (enum + feature-space predicate), not "Approve".
3. UI/log shows a candidate rule in plain English + support count.
4. Human promotes with explicit "yes, always".
5. Pass 2 = new transactions, same statistical shape, same counterparties, NOT easier.
6. STR climbs only because `rules.json` hit between cheap-match and routing.
7. Every auto-resolve stamps `rule_id`; every rule traces to `resolution_id`.
8. At least one EXCEPTION CLASS (N siblings) disappears — one memoized txn id is a fail.

CFO chain (goes in the README): unresolved exception → human SLA queue → delayed close →
stale forecast → CFO distrusts the number. STR is the only number in that chain software can
shrink.

## Dependency graph

```
W0 skeleton
  └─ W1 §4 generator
       └─ W2 Agent 1 matching
            ├─ W3 Agent 2 routing          ⎤ parallel, both depend only on W2
            ├─ W4 Agent 4 guardrail        ⎦
            └─ W5 Agent 3 learning           needs W2 + W3 + W4
                 └─ W6 Agent 5 scoreboard
                      └─ W7 README + demo + AO usage
```

No other parallelism. W5 does not start early "to save time" — it needs W2, W3, and W4 merged.

## Parallelism rules

- One card owns a named file set. Two live workers never edit the same files.
- W3 and W4 may run concurrently once W2 is merged — they touch disjoint packages
  (`routing/**` vs `guardrail/**`) and both only *read* Agent 1's output files.
- If two cards ever need the same file, split a tiny follow-up card instead of letting both edit it.

## Reviewer + merge rules

- After each worker opens a PR: run a reviewer against it before it's mergeable.
- Reviewer checklist: acceptance tests exist and pass; Write-only files respected (no edits
  outside the card's file set); L2 isolation if relevant (no `match_links.csv` import in
  Agents 1/2/4 — grep + AST test); no float money; no `datetime.now()` in routing; byte-identical
  reruns where the card requires it.
- Findings go back to the SAME worker, not a new one.
- Human merges. Orchestrator does not merge unless explicitly told it may.
- BOARD.md status is updated by the orchestrator on every state change (todo → spawned →
  in review → merged → blocked).

## Standing engineering laws (cite the relevant ones in every spawn)

- L1 File spine only. No agent imports another agent's internals.
- L2 Agents 1, 2, 4 NEVER read `match_links.csv`. Grep + AST test. Only generator tests and
  Agent 5 may use ground truth.
- L3 Money is `Decimal` in memory. CSV amounts are 2-decimal strings. Never float.
- L4 `(seed, pass_number, n_cases)` ⇒ byte-identical outputs, two runs.
- L5 Two RNG streams: counterparties from seed only (~800, shared across passes). Case stream
  from `(seed, pass_number)`.
- L6 Pass 2 must not be easier than pass 1. STR climb = learned rules only.
- L7 SLA/clock uses explicit `as_of`. Never `datetime.now()`.
- L8 Owner id = `blake2b(counterparty_key)`, never Python `hash()`.
- L9 LLM expensive tier is a stub seam: `llm_is_stub=True`, `llm_calls` measured. Do not block
  on TensorMux/Docker.
- L10 Capacity/settlement in integer cents. Duplicates never double-settle.
- L11 Learned rules are predicates in the MATCHER's feature vocabulary (normalized
  counterparty key, amount-delta bucket, reference-transform type, currency). Not a new
  embedding space. Not "txn #48213 is fine".
- L12 A learned rule is vetoed if Agent 4 would block or hold that line.
- L13 `manual_one_off` and `no_pattern` are first-class resolution types. They must not become
  auto-rules.
- L14 Demo promotion = explicit human "yes, always". Do not fake corroboration counts.
- L15 Kill list: no multi-currency/FX product, no CFO analytics dashboard, no semantic
  org-chart ownership, no pre-baked 98% claims.
- L16 Known matcher limitation (near-relative decoy names ~0.84 fuzzy) is documented and
  regression-pinned. Do not "fix" it with a full-ledger scan.
- L17 Worker stops when its acceptance is green. It does not start the next agent.

---

## Resolved decisions (partner lock, round 1)

These are standing rules, not suggestions. Cards below already reflect them.

- **Q1 — Demo surface:** No new card, no web UI, no demo-harness worker. The CLI lives inside
  W5 (`resolve`/`promote`) and W6 (`scoreboard`). If a worker proposes React/HTML for judges,
  refuse — kill-list spirit (L15) stands.
- **Q2 — Sponsors:** Stub-only. TensorMux keeps the L9 seam, no Docker work, no container card.
  Neatlogs and Dodo Payments are out of v1 entirely. No sponsor card gets spawned without the
  human explicitly saying go, even if W7 finishes early.
- **Q3 — Overlay:** Disclosed, not hidden. Overlay rows are labeled in the data; the printed
  summary and README say plainly that the class-elimination moment was seeded to be visible in
  one run. L6 (pass 2 not easier) still holds regardless.
- **Q4 — Ownership framing:** Named as a known limitation in the README, same style as the
  decoy-name and TensorMux limitations. Never sold as "routes to the right human in the org."
- **Q5 — W4 vs Agent 1 interlock:** PDF reading confirmed. Agent 4's policy book is exactly
  `denied_party`/`duplicate_release` (block), `dual_control`/`out_of_period` (hold),
  `upstream_veto` (matches upstream severity). Zero-amount/sign-flip/currency-mismatch stay on
  Agent 1 (§5.6), already in W2.

---

## Cards

### CARD W0 — Repo skeleton
**Status:** merged (PR #1, `238774c`)
**Depends:** none
**Branch:** `w0-skeleton`
**Reads:** spec PDF (package names only)
**Writes / may touch:**
- `pyproject.toml` or `requirements.txt`
- `src/ledger_sense/__init__.py`
- `src/ledger_sense/{data,matching,routing,learning,guardrail,metrics}/__init__.py`
- `tests/__init__.py`
- `.gitignore` (ignore `data/pass*/*.csv` bulk; do not ignore generator code)
- `README.md` stub

**Must implement:**
- Installable package (`pip install -e .`)
- Empty agent packages so later workers have a home
- Test runner configured (pytest)

**Does not:** generator logic, schemas beyond empty packages, any agent code.

**Acceptance:**
1. `pip install -e .` succeeds
2. `pytest` collects 0 tests and exits 0
3. No agent code

**Stop. Do not start W1.**

---

### CARD W1 — §4 data model & generator
**Status:** merged (PR #2, `dfea777`). **No GitHub review was recorded on this PR** — W2
will not skip the reviewer step to make up for it.
**Note:** the overlay is OFF by default — `--overlay` is an explicit opt-in flag on the
generator CLI. The demo / pass-1 command for the class-elimination moment MUST pass `--overlay`;
a plain run will not plant the fee_offset siblings.
**Depends:** W0 merged
**Branch:** `w1-generator`
**Reads:** spec §4
**Writes / may touch:**
- `src/ledger_sense/data/**`
- `tests/test_generator.py`, `tests/test_generator_invariants.py`
- CLI or `python -m` entry for `generate`
- tiny fixture + exact generate command committed; do not commit full 25k-line CSVs unless human says so

**Must implement:**
- Tables: `LedgerEntry` → `ledger.csv`; `BankTransaction` → `bank.csv`; `MatchLink` → `match_links.csv`
- IDs and columns exactly as spec §4.1
- Cardinality: mostly 1:1; duplicates/partials 1:2; orphans 1:0 or 0:1
- Defect mix §4.2: clean 57.0, wrong_reference 7.0, partial_payment 6.0, out_of_order 6.0,
  duplicate 5.0, missing_reference 5.0, fx_rounding 4.0, malformed 2.5, negative_amount 2.0,
  orphan_bank 2.0, orphan_ledger 2.0, zero_amount 1.5
- Baseline name noise ~85%; KEY4 INVARIANT: first 4 alphanumerics of canonical name survive
  every noise variant (assert)
- Two RNG streams as L5
- Reference command: `seed=42, pass_number=1, n_cases=25000`
- Pass 2 path: `seed=42, pass_number=2`, same `n_cases` → new txns, non-overlapping references,
  same counterparties, same defect shape, NOT easier
- DEMO OVERLAY (answers PDF §15 open question A, locked Q3): inspect how many exception-shaped
  siblings share (counterparty ≈ X AND amount_delta bucket AND ref pattern). If no class has ≥8
  siblings that will survive Agent 1 as exceptions, add an explicit overlay flag that plants
  12–20 siblings of one fee_offset-shaped defect without changing the rest of the mix more than
  documented
- **Overlay must be labeled, not hidden (locked Q3):** every overlay-planted row carries a flag
  or note (e.g. `match_links.note` says `overlay:fee_offset`, or an `is_overlay` column) so it's
  auditable, not indistinguishable from organic data. Overlay presence and count must not change
  what "not easier" means for pass 2 — L6 still holds: pass 2's overlay siblings (if any) are
  generated fresh from the case stream, not copied.

**Must not:** matching, routing, any read of `match_links.csv` from a future agent package.

**Acceptance:**
1. Two pass-1 generations → byte-identical CSVs
2. Defect histogram within a tight documented tolerance of §4.2
3. key4 invariant test
4. Pass 2 references disjoint from pass 1; counterparty universe equal
5. Printed summary: row counts, defect histogram, unique counterparties, overlay class name +
   sibling count called out explicitly (not buried in the histogram)
6. Every overlay row is labeled and the label is queryable/testable (not just a comment)
7. Money never stored as float in generator

**Laws:** L3, L4, L5, L6, L15, L16
**Stop. Do not start Agent 1.**

---

### CARD W2 — Agent 1 Matching
**Status:** merged (PR #3, `521dadc`). Measured seed=42 pass 1, overlay OFF: cheap-tier
83.93% (22872/27250), overall with stub 89.31%, precision 1.000000, 1250/1250 duplicate cases
one settlement + one duplicate, 0 bait/orphan auto-matches, `llm_calls==0`, two full-output
reruns byte-identical. Cheap-tier target (~88–89%) was missed at 83.93% — **do not reopen W2
for this**; it was reported honestly by the worker rather than tuned to hit the number, and the
overall-with-stub figure (89.31%) clears its own target. Note for the record.
**Depends:** W1 merged
**Branch:** `w2-matching`
**Reads:** `data/pass1/ledger.csv`, `data/pass1/bank.csv` ONLY
**Writes / may touch:**
- `src/ledger_sense/matching/**`
- `tests/test_matching.py`, `tests/test_matching_isolation.py` (forbids `match_links` import)
- output path for `match_outcomes.csv`, `ledger_settlements.csv`

**Must implement (spec §5):**
- Blocking `candidates()`: union of (1) exact normalized reference (2) `by_key4` ∩ ±4
  amount-bucket window (3) only if 1&2 empty, `by_key4` filtered to plausible part-payment
  ratio. Cap 40, closest amounts on overflow. No full-scan fallback. Empty block → `no_candidate`
- Five features 0–100, weights: reference 40, amount 30, name 20, date 7, currency 3
- No reference → drop that feature, renormalize remaining to 100
- Wrong reference scores 0.0 but KEEPS 40-point weight
- Short-circuit: reference==1.0 AND amount exact AND currency==1.0 → 100
- Amount classes on integer cents: exact / fx (delta ≤ max(3.50, 0.5%) and ≤¼ of entry) /
  partial (15–85%) / conflict
- Auto-match: no interlock veto, score≥88.0, margin≥6.0 or single candidate, capacity remains
- `PARTIAL_WITH_EXACT_REFERENCE`: score≥78.0, reference exact, amount=partial, capacity
  remains, name≥0.70
- Escalate 45–88; reject below 45
- Greedy assignment desc (score, bank_txn_id) against one capacity ledger in cents
- Duplicate: first leg wins; second `duplicate_of_matched`, `matched_amount=0.00`
- Interlock veto inside Agent 1: 0.00 vs nonzero, flipped sign, currency mismatch on the best
  candidate — never auto-match; re-check after stub LLM too
- LLM seam: protocol exists; ship none + stub adjudicator; `llm_is_stub=True`; `llm_calls==0` measured
- Output columns exactly as spec §5.8

**Targets on seed=42 pass 1 (acceptance, not marketing copy):** cheap-tier ~88–89%; overall
with stub ~92–93%; precision of matched ≥0.999 vs ground truth IN TESTS ONLY; duplicates
flagged 100% never double-settled; guardrail bait auto-matched 0; orphan bank matched 0; two
runs byte-identical CSVs.

**Does not:** routing, `rules.json`, dashboards, reading `match_links.csv` in `matching/`.

**Laws:** L1, L2, L3, L4, L9, L10, L16
**Stop.**

---

### CARD W3 — Agent 2 Ownership / Routing
**Status:** spawned
**Depends:** W2 merged
**Branch:** `w3-routing`
**Reads:** Agent 1 output files only
**Writes / may touch:**
- `src/ledger_sense/routing/**`
- `tests/test_routing.py`, `tests/test_routing_isolation.py`
- `exceptions.csv`, `owner_queues.csv`

**Must implement (spec §6):**
- Five categories only: `duplicate`, `amount_mismatch`, `timing`, `unidentified_counterpart`, `suspect_posting`
- Ordered first-hit bank-side classifier exactly as spec §6.2
- Name floor 0.70 — never more generous than the matcher
- Book side: `never_settled` → timing; `partially_settled` residual ≥15% → timing; <15% → amount_mismatch
- Pair-and-suppress: bank subject whose top candidate is an unclaimed ledger subject emitted
  once as `subject_kind=pair`
- Roster of named people (3 AR / 3 AP / 3 recon-ops / 2 controllers)
- Desk: `suspect_posting`→controller, `duplicate`→recon_ops, else AR if inbound else AP
- Individual: `blake2b(counterparty)`, not transaction id
- No capacity spill; report load on `owner_queues.csv`
- `sla_hours = BASE[category] × SEVERITY[P1=0.5,P2=1.0,P3=1.5]`
- `suspect_posting` always P1, 4h base
- Else amount buckets ≥10000 P1, ≥1000 P2, else P3
- Clock from run `as_of`; `at_risk` <25% remaining; `breached` now≥due_at
- Collision: unique `exception_id` hard error; independent clocks; queues sorted
  `(due_at, exception_id)`; queue counts reconcile

**Targets:** STR ~87–88% (no exception row = straight-through); 100% routed rows have
category/named owner/clock; GT duplicates → duplicate 100%; guardrail bait → suspect_posting
≥95%; byte-identical reruns.

**Does not:** `rules.json`, release decisions, `datetime.now()`, `hash()`.

**Laws:** L1, L2, L4, L7, L8
**Stop.**

---

### CARD W4 — Agent 4 Escalation / Guardrail
**Status:** spawned — running as session `ledger-sense-9` ("W4 guardrail"), branch
`w4-guardrail`. (Prior attempt `ledger-sense-7` died with zero pushed work — no branch, no PR —
replaced, not resumed.) Running in parallel with W3 (`guardrail/**` vs `routing/**` — disjoint
file sets, per the parallelism rule below).
**Depends:** W2 merged (MAY run in parallel with W3)
**Branch:** `w4-guardrail`
**Reads:** `ledger.csv`, `bank.csv`; Agent 1–2 outputs only for corroboration / carrying
upstream flags
**Writes / may touch:**
- `src/ledger_sense/guardrail/**`
- `tests/test_guardrail.py`, `tests/test_guardrail_isolation.py`
- `release_decisions.csv`, `guardrail_audit.csv`, `held_settlements.csv`, `policy_applied.json`

**Must implement (spec §8 — exact policy book, per PDF §8.1):**
- Five rules, two verdicts:
  - `denied_party` → **block** (counterparty matches a hardcoded, JSON-overridable compliance list)
  - `duplicate_release` → **block** (re-detects duplicates independently of Agent 1's flag — genuine corroboration, on purpose)
  - `dual_control` → **hold** (amount exceeds a materiality threshold, e.g. $200,000)
  - `out_of_period` → **hold** (value date outside a configurable window, e.g. 30 days, of `as_of`)
  - `upstream_veto` → matches upstream severity (carries forward anything Agents 1–2 already flagged suspect)
- Independent re-derivation everywhere — never self-report an upstream flag as the basis for a verdict
- Deterministic verdicts: `allow | block | hold`
- Version the policy book (e.g. `2026.09-1`)
- Expose `would_block_or_hold(line, candidate_rule) → veto` for W5 to call
- Never block a clean cheap-tier match (independent re-check, not self-report)
- Precision test (write on day one): a denied-party token must never fire on an unrelated but
  textually similar name — e.g. list entry `ORBEX` must not block `ORBEXIA CORP`
- Output contract per spec §8.2: `release_decisions.csv` (one row per bank line, every run:
  `bank_txn_id, verdict, primary_rule, all_firing_rules, reason, upstream_context,
  required_approvals, policy_version`), `guardrail_audit.csv` (one row per rule firing),
  `held_settlements.csv` (held population), `policy_applied.json` (exact policy book enforced)

**Acceptance (spec §8.3, AC1–AC4):**
1. AC1 — every bank line receives exactly one release decision
2. AC2 — every block/hold names a rule, a reason, upstream context, and (for holds) required approvals
3. AC3 — every audit finding cites a rule present in the applied policy
4. AC4 — no blocked line is releasable by any route, including approvals — independently re-derived, not self-reported
5. Printed allow/block/hold split FROM THE RUN (reference ~86.8/10.4/2.8 — do not hardcode as inputs)
6. 0 clean-matched lines blocked
7. Byte-identical reruns
8. Isolation test: no `match_links` import

**Does not:** change matcher feature weights; treat zero-amount/sign-flip/currency-mismatch as
Agent 4 policies — those are Agent 1's guardrail-interlock (§5.6), already implemented in W2.

**Laws:** L1, L2, L4, L7
**Stop.**

---

### CARD W5 — Agent 3 Resolution-Learning (CORE BET)
**Status:** todo
**Depends:** W2 AND W3 AND W4 merged
**Branch:** `w5-learning`
**Reads:** `exceptions.csv`; one structured human resolution; Agent 4 veto API
**Writes / may touch:**
- `src/ledger_sense/learning/**`
- `tests/test_learning.py`
- `rules.json`
- CLI entrypoints `ledger_sense resolve` and `ledger_sense promote` (locked Q1 — this is the
  entire demo interaction surface, no separate UI/harness card exists or will exist)
- thin insert hook that Agent 1 pass-2 calls — insertion BETWEEN cheap tier and routing
  escalate, not a rewrite of matching internals. If the hook must live in `matching/`, it is a
  20-line `consume_rules()` only; coordinate as a follow-up card since W2 is already merged.

**Must implement:**
- `resolution { exception_id, resolution_type: fee_offset | reference_transform |
  counterparty_alias | timing_tolerance | manual_one_off | no_pattern, evidence (feature-space
  predicate), rationale (free text, audit only), resolved_by, resolved_at }`
- Candidate rule = predicate over matcher features, e.g.
  `counterparty≈Acme AND 0<amount_delta≤3 AND reference matches`
- After one resolution: `status=candidate`, show English predicate + support count against
  current exception pile
- Promote only on explicit confirmation for this hackathon ("yes, always" — L14, not a
  corroboration count)
- On promote: write `rules.json`; each rule has `rule_id` + `resolution_id`
- Pass 2: lines that would have escalated are checked against `rules.json` first; hit resolves
  and stamps `rule_id`
- Guardrail veto before fire (`would_block_or_hold` from W4)
- **CLI, locked Q1 — this is the demo surface, terminal only, no web/GUI:**
  - `ledger_sense resolve` takes the structured fields directly (`exception_id`,
    `resolution_type`, the evidence/predicate, `rationale`) — flags or a prompted form, worker's
    choice, but never a free-text "approve". After running it, print the candidate predicate in
    plain English, its support count against the current exception pile, and `status=candidate`.
  - `ledger_sense promote <rule_id> --confirm yes-always` is the only way to promote — no other
    path writes `rules.json`. On success, write `rules.json` and print `rule_id ← resolution_id`.

**Acceptance:**
1. Every rule has `resolution_id`
2. ≥1 class with N≥8 (or overlay size) auto-resolves in pass 2
3. 0 learned auto-resolves on block/hold
4. STR pass2 > STR pass1; delta explained by rule hits
5. `manual_one_off` does not create a rule
6. Tests prove pass-2 climb is rule application, not easier data
7. `ledger_sense resolve` and `ledger_sense promote <rule_id> --confirm yes-always` work
   end-to-end from the terminal and produce the exact printed output specified above

**Does not:** memoize `bank_txn_id`; CFO UI; web UI of any kind; corroboration-count promotion.

**Laws:** L1, L6, L11, L12, L13, L14
**Stop.**

---

### CARD W6 — Agent 5 Metrics Orchestrator
**Status:** todo
**Depends:** W5 merged
**Branch:** `w6-metrics`
**Reads:** pass1 + pass2 outputs of agents 1–4; `match_links.csv` ALLOWED here only
**Writes / may touch:**
- `src/ledger_sense/metrics/**`
- `tests/test_metrics.py`
- `scoreboard.json` (or equivalent)
- CLI entrypoint `ledger_sense scoreboard` (locked Q1 — terminal only, part of the demo surface)

**Must implement:**
- Run or consume two full passes
- Side-by-side: STR, exceptions remaining, exceptions eliminated BY CLASS, learned-rule count
- Trace table: auto-resolved row → `rule_id` → `resolution_id`
- Refuse to print a pass-2 number that was not computed from files on disk
- **CLI, locked Q1:** `ledger_sense scoreboard` prints the pass1-vs-pass2 comparison
  (STR, exceptions by class, learned-rule count, traces) computed only from files already on
  disk — terminal output, no rendering step required. Piping it through `ao preview` later for
  a nicer view on camera is fine; that is not a build requirement and not a new card.

**Does not:** fancy CFO dashboard, charts that invent numbers, any web UI.

**Laws:** L2, L4, L6
**Stop.**

---

### CARD W7 — Ship surface
**Status:** todo
**Depends:** W6 merged
**Branch:** `w7-ship`
**Writes:** `README.md`, `DEMO.md`

**Must include:**
- §1 positioning sentences ("Finance software automates known rules...")
- How to generate data and run pass 1, resolve, pass 2
- Measured numbers from this machine
- How AO was used: orchestrator + list of workers + branches + PRs + reviewers
- 60–90s demo script that is literally the CLI commands in order, run in a terminal on camera:
  `generate` (pass 1) → `ledger_sense resolve` → `ledger_sense promote <rule_id> --confirm
  yes-always` → `generate` (pass 2) / rerun → `ledger_sense scoreboard`. No React/HTML, no
  screen mockup — terminal output is the demo (locked Q1).
- **Sponsor disclosure paragraph (locked Q2, answers PDF §15 open question C), verbatim intent:**
  state plainly that this environment has no Docker and no `TENSORMUX_*`/`NEATLOGS_*`/`DODO_*`
  env vars; the cheap tier is the measured path; the expensive/LLM tier is a documented stub
  (`llm_is_stub=True`, `llm_calls` measured); Neatlogs and Dodo Payments are out of this build
  entirely.
- **Overlay disclosure sentence (locked Q3), exact text:**
  "Pass-1 includes a labeled overlay of N sibling fee-offset exceptions so the learning moment
  is visible in one run; matching and resolution logic are not scripted."
- **Ownership-framing limitation bullet (locked Q4), exact text, alongside the decoy-name (§5.4)
  and TensorMux limitations:**
  "Agent 2 assigns a named person from a fixed roster via blake2b(counterparty). It does not
  discover the real organizational owner of a dependency. Routing exists to feed learning and
  SLA, not to replace org design."
- The demo script and README must never claim Agent 2 "routes to the right human in the org" —
  reject that framing if a draft contains it.

**Does not:** new product features, sponsor integration work beyond the disclosure paragraph
(Neatlogs/Dodo/TensorMux container work is out of v1 — locked Q2; do not spawn that card
without the human explicitly saying go).

**Stop.**
