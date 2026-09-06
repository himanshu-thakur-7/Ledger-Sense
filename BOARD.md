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
**Status:** merged (PR #4, `d54d424`, squash-merged by orchestrator per explicit human
authorization — no GitHub review, human asleep-adjacent). Measured seed=42 pass 1, overlay
off, real 25k-case batch (matching rerun locally to reproduce W2's 83.93% cheap-tier before
routing): STR=87.51% (target ~87–88%), GT duplicates → duplicate 100%, guardrail bait →
suspect_posting 100% (target ≥95%), 100% of routed rows carry category+owner+clock, two full
reruns byte-identical, 176/176 repo tests green.
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
**Status:** merged (PR #5, `838f09b`, squash-merged by orchestrator per explicit human
authorization — no GitHub review, human asleep-adjacent). (Prior attempt `ledger-sense-7` died
with zero pushed work — replaced by `ledger-sense-9`, not resumed.) Real seed=42 pass-1 run,
explicit full-batch period (2025-12-01..2026-07-01): allow 90.58%, block 8.72%, hold 0.69% —
block matches the spec's ~10.4% reference almost exactly. Default single-calendar-month period
against this generator's ~6-month `value_date` spread produces a much higher hold rate by
design (documented, CLI-overridable). `duplicate_release` independently recovers all 1,250
ground-truth duplicate legs 1:1; 0 clean cheap-tier auto-matches ever blocked. Isolation clean,
no float, byte-identical reruns, 147/147 full-suite tests green at merge time.
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
**Status:** merged (PR #6, `6cb93bc`, squash-merged by orchestrator per explicit human
authorization). Verified before merge: zero `match_links`/matching-or-routing-internals
references in any `learning/` source file (only in its own isolation test); only cross-agent
import is guardrail's public `would_block_or_hold`; `pyproject.toml`'s only change is a
console-script entry + pytest marker. Real seed=42 measurement on an independently-generated
pass-2 batch: a promoted fee-offset-shaped predicate (no counterparty in it — generalizes by
shape) resolved 8/8 matching escalated lines, 0 vetoed, STR 23839/27270 → 23847/27270 (delta ==
rule-hit count exactly), the exception class went 8 rows → 0 rows. A control run without
`rules.json` still shows all 8 exceptions (L6 held). A misconfigured guardrail period correctly
vetoed all 8 candidate hits in a real run — L12 confirmed live, not just unit-tested.
`manual_one_off` created zero rule entries. 251/251 tests passed in the worker's own run (data
present); 249/2-skipped in orchestrator's post-merge sanity check (no local `data/pass1`, as
expected — that data is gitignored).
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
**Status:** merged (PR #7, `18cbe83`, squash-merged by orchestrator per explicit human
authorization). Verified before merge: zero `ledger_sense.*` imports anywhere in `metrics/`
(stricter than the L2 carve-out required), zero `float()`, `pyproject.toml`'s only change is
one console-script line. Real seed=42 two-pass run: pass-1 STR 87.51% (matches W3's own
independently-recorded seed=42 number — confirms deterministic reproduction across agents), 
pass-2 87.70%/87.69% (naive/real), 75 rule-driven auto-resolves at 100% trace coverage, 1
learned rule. 924 exception classes eliminated overall, honestly split: ~85 attributable to the
promoted rule's exact shape, the rest ordinary two-draw variance — not inflated into one
headline number. Byte-identical `scoreboard.json` reruns confirmed at 25k scale. 277 tests
passed at merge.
**Note for W7:** the actual scoreboard binary is `ledger_sense-scoreboard scoreboard
--pass1-dir ... --pass2-dir ...` (a second `[project.scripts]` entry under the literal
`ledger_sense` key would collide with W5's `resolve`/`promote`), not bare `ledger_sense
scoreboard` — use the real invocation in the demo script.
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
**Status:** merged (PR #8, `dd3540b`, squash-merged by orchestrator per explicit human
authorization). **BUILD COMPLETE — all 8 cards (W0–W7) on main.** Verified before merge: file
scope exactly README.md + DEMO.md, all four locked disclosures present verbatim (sponsor stub,
overlay — fired pass-2 this run at 13 siblings, honestly reported since pass-1's natural
cluster already met the gate without it — ownership framing, decoy-name limitation whose
referenced regression test was confirmed to actually exist on main), no "routes to the right
human in the org," no web UI, correct `ledger_sense-scoreboard` invocation, numbers from the
worker's own real run rather than copied from a prior PR body. Post-merge sanity check green:
277 passed, 2 skipped (expected — slow full-pipeline tests need generated data).
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

---

# Ledger Sense v2 — real integrations

Source of truth: `LEDGER-SENSE-v2-PRD.md` (repo root). v1 (W0–W7 above) is complete and
unmodified by v2 — v2 only fills the three seams v1 deliberately stubbed (LLM adjudication,
Dodo Payments, Neatlogs), disclosed in v1's own README/BOARD.md. Same rule as v1: if a v2 card
and the v2 PRD ever disagree, stop and escalate — don't invent a third version. v1's PDF
remains the source of truth for anything v1-shaped (schemas, thresholds, filenames already
shipped); v2 does not relitigate those.

## v2 dependency graph

```
W8 config/secrets foundation (no dependencies)
  ├─ W9  OpenAI matching adjudicator         ⎤
  ├─ W11 Dodo Payments sandbox source        ⎥ fully parallel, mutually disjoint files,
  ├─ W12 OpenAI rationale assist              ⎥ all depend only on W8
  └─ W13 OpenAI routing fallback             ⎦
       └─ W10 Neatlogs tracing (wraps every entrypoint — must come AFTER W9/W11/W12/W13,
       │  not parallel with them; see W10's card for why the original PRD's parallel plan
       │  would have collided on 3 of 4 entrypoint files)
       └─ W14 metrics v2 + docs + live smoke test (needs W9, W10, W11, W12, W13 all merged)
```

W9, W11, W12, W13 spawn together now that the locked decisions below are settled. W10 waits
for all four. W14 waits for W10 plus the rest.

## Locked decisions (human-approved, round 1)

1. **Dodo/ledger-pairing (W11):** pull-then-synthesize — pull existing Dodo sandbox
   transactions first, then synthesize matching `ledger.csv` rows around them, mirroring the
   generator's defect-injection logic. Fully automated, no manual dashboard clicking per demo.
2. **OpenAI model + cost cap (W9/W12/W13):** cheap tier (`gpt-4o-mini`-class), **$1.00/run**
   cap, both overridable via `LEDGER_SENSE_OPENAI_MODEL` / `LEDGER_SENSE_LLM_COST_CAP_USD`.
3. **Shared LLM client:** one thin wrapper module (`llm_client.py`, built in W8) is the single
   point of retry/timeout/cost-cap/redaction enforcement for W9, W12, and W13. No independent
   reimplementations.
4. **API keys:** the human sets `OPENAI_API_KEY` / `DODO_API_KEY` / `DODO_ENVIRONMENT=sandbox`
   / `NEATLOGS_API_KEY` directly via `ao project set-config ledger-sense --env "KEY=..."`.
   Never pasted through chat, never committed, never logged. Until they're set, W9–W13 build
   and test fully mocked/offline; only W14's live smoke test needs the real values, and only
   when the human is ready to run it.

## v2 standing laws (extend L1–L17 above; cite the relevant ones in every v2 spawn)

- **L18** Absence of any live-mode API key must degrade gracefully to v1 stub/synthetic/
  no-tracing behavior — never crash, never change v1's zero-key output.
- **L19** No secrets hardcoded or logged anywhere. Neatlogs spans and any debug output must
  redact credential-shaped values before emission.
- **L20** Every new external-API code path is unit-tested against a mocked client. The full
  pytest suite stays 100% offline/deterministic — zero live network calls, zero API spend.
- **L21** LLM output at every seam (adjudication, rationale-assist, routing fallback) is
  bounded to its existing seam only — never given authority beyond what v1's deterministic
  layer already couldn't resolve. The guardrail always independently re-derives and is never
  bypassed or trusted-through.
- **L22** Per-run cost/call caps, timeouts, and bounded retries are mandatory on every external
  API client, enforced through the shared `llm_client.py`.

## v2 explicitly not doing (mirrors v1's §14 discipline)

No database/persistent-store migration; no API/service wrapper — stays CLI/batch/files; no
UI/dashboard; no production deployment/infra; no multi-tenant support; no live (non-sandbox)
Dodo payment processing; no OpenAI fine-tuning; no widening of the matcher's or guardrail's
decision authority beyond the bounded seams below.

---

### CARD W8 — Config/secrets foundation
**Status:** merged (PR #9, `f8dcbd8`, squash-merged by orchestrator per standing human
authorization). Verified: zero OpenAI/Dodo/Neatlogs SDK imports anywhere in the package;
`.env.example` placeholders only, no real secrets (GitGuardian clean); base install stays
dependency-free in a clean venv. **Orchestrator follow-up:** W8's own PR flagged that `.env`
wasn't in `.gitignore` — fixed directly (one-line, non-code hygiene) before any real secret
could land in a working tree, since real API keys are now configured on the project. 330
passed, 2 skipped at merge (v1's 277 + 53 new, all against mocked transports).
**Depends:** none (v2 foundation; builds on merged v1)
**Depends:** none (v2 foundation; builds on merged v1)
**Branch:** `w8-config`
**Reads:** `LEDGER-SENSE-v2-PRD.md`, `src/ledger_sense/matching/adjudication.py` (Protocol
shape, read only — do not modify)
**Writes / may touch:**
- `src/ledger_sense/config.py` (new)
- `src/ledger_sense/llm_client.py` (new — the shared wrapper, locked decision 3)
- `.env.example`
- `tests/test_config.py`, `tests/test_llm_client.py`
- `pyproject.toml` (add `[project.optional-dependencies]` extras only: `llm`, `dodo`,
  `tracing` — base install stays zero-dependency, v1's own must-hold property)

**Must implement:**
- `config.py` reads from environment/`.env`: `OPENAI_API_KEY`, `LEDGER_SENSE_OPENAI_MODEL`
  (default `gpt-4o-mini`), `LEDGER_SENSE_LLM_COST_CAP_USD` (default `1.00`), `DODO_API_KEY`,
  `DODO_ENVIRONMENT` (default `sandbox`), `LEDGER_SENSE_DATA_SOURCE` (default `synthetic`),
  `NEATLOGS_API_KEY`. Exposes central functions (`openai_enabled()`, `dodo_enabled()`,
  `tracing_enabled()`) — every later module calls these, never touches `os.environ` directly.
- `.env.example` lists every var with a placeholder + comment, zero real secrets (grep-tested).
- `llm_client.py`: a thin wrapper class around an injectable "transport" callable — bounded
  retries with backoff, request timeout, per-run cumulative cost/call cap enforcement
  (short-circuits before the cap is exceeded), a redaction helper for logging, and response
  caching keyed by a caller-supplied key. Does NOT call OpenAI itself — W9 plugs the real SDK
  call in behind it; tests inject a mock transport.
- `pyproject.toml` extras: `llm = ["openai>=1.0"]`, `dodo = [...]`, `tracing = ["neatlogs"]` —
  the base package install must remain exactly as dependency-free as v1 shipped it.
- Graceful degradation: if `OPENAI_API_KEY` is absent, `config.openai_enabled()` returns
  `False` and nothing downstream may ever attempt a call (same pattern for Dodo/Neatlogs).

**Does not:** any actual OpenAI/Dodo/Neatlogs SDK calls; any change to matching/routing/
guardrail/learning/metrics/data logic; any BOARD.md/README changes (W14 owns docs).

**Acceptance:**
1. `pip install -e .` (base, no extras) still succeeds with zero new *required* dependencies
2. `config.py`'s enabled/disabled logic has full unit-test coverage for all three integrations
3. `llm_client.py`'s retry/timeout/cost-cap logic tested against a mocked transport only
4. `.env.example` exists, documents every var, contains no real secret values
5. Full v1 test suite (277 tests) still passes unmodified

**Laws:** L18, L19, L20, L22
**Stop. Do not start W9.**

---

### CARD W9 — OpenAI matching adjudicator
**Status:** merged (PR #13, `f1fc691`, squash-merged by orchestrator per standing human
authorization). Verified before merge: judges only the already-computed top candidate (no new
candidate logic, L21), retries/timeout/cost-cap/cache all via W8's llm_client.py (never
reimplemented), falls back to StubAdjudicator on any failure. CLI default (`stub`) stays
byte-for-byte unchanged regardless of ambient `OPENAI_API_KEY` -- only explicit `--adjudicator
auto` opts in. 14 dedicated tests incl. a full CSV-diff regression vs v1 and a cost-cap-mid-batch
fallback test. Full suite 344 passed at merge.
**Depends:** W8 merged
**Branch:** `w9-openai-adjudicator`
**Reads:** `LEDGER-SENSE-v2-PRD.md`, `matching/adjudication.py` (Protocol, read only),
`config.py` + `llm_client.py` (from W8, import)
**Writes / may touch:**
- `src/ledger_sense/matching/llm_adjudicator.py` (new)
- `tests/test_llm_adjudicator.py`
- **The one existing-file exception:** the single call site in `matching/__main__.py` (and/or
  `engine.py`) that currently hardcodes `StubAdjudicator()` — swap for a config-driven factory
  call. Nothing else in `matching/` changes; this is the whole point of the seam v1 built.

**Must implement:**
- `OpenAIAdjudicator` implementing the `Adjudicator` Protocol exactly (`llm_is_stub=False`,
  `llm_calls` counter, `model` from config), operating only on the same `Question`/`Verdict`
  dataclasses `adjudication.py` already defines — no new candidate-generation logic.
- Structured JSON response: `{decision: match|no_match|needs_human, confidence, rationale}`.
  `temperature=0`, timeout, bounded retries via `llm_client.py`, the locked $1/run cost cap,
  response cache keyed by `(ledger_id, bank_txn_id)`.
- On API failure or cap breach: falls back to `StubAdjudicator`'s decision for that batch —
  never crashes, never blocks.
- A factory function (e.g. `get_adjudicator()`) that returns `OpenAIAdjudicator` if
  `config.openai_enabled()` else `StubAdjudicator` — this is the one line `__main__.py`/
  `engine.py` calls instead of hardcoding the stub.

**Does not:** touch matching's feature scoring, blocking, thresholds, or capacity logic; touch
routing/guardrail/learning/metrics; widen adjudication beyond the existing gray-zone seam.

**Acceptance:**
1. Unit tests against a mocked OpenAI client — zero live network calls
2. Regression: with `OPENAI_API_KEY` unset, output is byte-identical to v1's `StubAdjudicator`
3. With a mocked "always match" response, a gray-zone candidate v1's stub would have escalated
   is now auto-matched, and `llm_calls`/`llm_is_stub=False` flow into `match_outcomes.csv`
   (already-existing columns — verify, don't add new ones)
4. Cost-cap test: mocked transport simulates hitting the cap mid-batch; remaining candidates
   fall back to stub behavior rather than crashing
5. Cache test: the same `(ledger_id, bank_txn_id)` pair adjudicated twice in one run calls the
   mocked transport exactly once

**Laws:** L1, L9, L18, L20, L21, L22
**Stop.**

---

### CARD W10 — Neatlogs tracing
**Status:** spawned (now that W9/W11/W12/W13 are all merged, per the collision-avoidance sequencing below)
**Depends:** W8 merged AND W9 AND W11 AND W12 AND W13 all merged (orchestrator correction from
the original PRD's "parallel with W9/W11/W12/W13": W10 wraps every agent entrypoint — `data`,
`matching`, `routing`, `guardrail` `__main__.py` files plus `learning/cli.py` and
`metrics/cli.py`. That collides with W9 (`matching/__main__.py`), W11 (`data`'s entrypoint,
`--source dodo` flag), and W12 (`learning/cli.py`'s `resolve` command) — three of the other
four v2 cards. W13 is the one exception: it edits `routing/engine.py`, not `routing/
__main__.py`, so it doesn't actually collide with W10 — but running W10 dead last, after all
four, is simpler and safer than tracking that one exception by hand. W9/W11/W12/W13 ARE fully
mutually disjoint from each other (each owns its own agent package + its own entrypoint file)
and run in parallel; W10 is the only one that must wait for all of them.)
**Branch:** `w10-tracing`
**Reads:** `LEDGER-SENSE-v2-PRD.md`, `config.py` (`tracing_enabled()`), each agent's existing
`__main__.py`/`cli.py` (read only, to find the minimal wrap point)
**Writes / may touch:**
- `src/ledger_sense/tracing.py` (new)
- One minimal wrapping edit in each of: `data/__main__.py`, `matching/__main__.py`,
  `routing/__main__.py`, `guardrail/__main__.py`, `learning/cli.py`, `metrics/cli.py` — a
  single decorator/context-manager call per entrypoint, nothing else in those files changes
- `tests/test_tracing.py`

**Must implement:**
- `traced_run(agent_name, **metadata)`: a context manager/decorator that starts a Neatlogs
  span if `tracing_enabled()`, else is a no-op — never raises, never blocks the pipeline if
  Neatlogs is unreachable.
- Span captures: agent name, duration, input/output row counts, guardrail allow/block/hold
  breakdown (guardrail entrypoint only), `llm_calls`/tokens/estimated cost (matching/learning/
  routing entrypoints only, when present).
- Redaction: any API-key/credential-shaped string is stripped before attaching to a span.
- `NEATLOGS_API_KEY` absent → zero Neatlogs SDK calls, zero overhead beyond a no-op.

**Does not:** touch each agent's actual logic beyond the one wrap point; add new CLI flags
beyond what `config.py` already exposes.

**Acceptance:**
1. With `NEATLOGS_API_KEY` unset, all 5 entrypoints run byte-identical to v1 output
2. Unit tests against a mocked Neatlogs client — zero live network calls
3. Redaction test: a fake API-key-shaped string in span metadata never reaches the (mocked)
   client's payload
4. A mocked client raising an error never crashes the CLI or changes its exit code/output

**Laws:** L18, L19, L20
**Stop.**

---

### CARD W11 — Dodo Payments sandbox source
**Status:** merged (PR #11, `c1cad8d`, squash-merged by orchestrator per standing human
authorization). Verified before merge: pull-then-synthesize followed exactly (locked decision
1), output proven matching-engine-compatible via a real integration test (not just schema
assertion), amounts Decimal/cents throughout (L3), missing-key path exits clean and nonzero.
Zero float, zero live network calls in tests. Full suite 362 passed at merge.
**Depends:** W8 merged (MAY run in parallel with W9/W10)
**Branch:** `w11-dodo-source`
**Reads:** `LEDGER-SENSE-v2-PRD.md`, `config.py`, `data/models.py` (`BankTransaction` shape,
read only), `data/generator.py` (defect-injection logic, read only — small documented
reimplementation is fine, same as other agents have done for shared vocabulary)
**Writes / may touch:**
- `src/ledger_sense/data/dodo_source.py` (new)
- `src/ledger_sense/data/dodo_pairing.py` (new — locked pull-then-synthesize strategy)
- `tests/test_dodo_source.py`, `tests/test_dodo_pairing.py`
- A CLI flag/entry so the generator's existing entrypoint can select `--source dodo` instead
  of pure synthesis

**Must implement:**
- **Pull-then-synthesize (locked decision 1):** call Dodo's sandbox API to list existing
  transactions (paginated), normalize each into the exact `BankTransaction` shape (amount as
  Decimal/cents — never float, law L3 — currency, reference/metadata, counterparty, timestamp,
  direction), dedup by Dodo transaction ID (idempotent — re-running never duplicates).
- For each pulled transaction, synthesize a paired `LedgerEntry` row using the same defect-mix
  proportions/logic as the synthetic generator (reuse/mirror, documented).
- `LEDGER_SENSE_DATA_SOURCE=synthetic|dodo` (default `synthetic` — v1's deterministic/CI
  behavior untouched unless explicitly opted in). Output paths/shapes identical to the
  synthetic path — matching/routing/guardrail/learning/metrics need zero changes to consume it
  (prove this with an integration test, not just a schema assertion).
- Document the exact pairing decision in the module docstring + a README note (W14) — not a
  silent guess.
- `DODO_API_KEY` absent + `--source dodo` requested → clean nonzero exit with a clear message,
  never a stack trace; the synthetic default path is completely unaffected.

**Does not:** touch matching/routing/guardrail/learning/metrics; any live (non-sandbox) Dodo
calls; any payment creation/processing — read-only listing of existing sandbox transactions.

**Acceptance:**
1. Unit tests against a mocked Dodo client — zero live network calls, zero sandbox API spend
2. Dodo-sourced output rows pass the exact same schema validation as `generator.py`'s output
3. Idempotency: pulling the same mocked transaction list twice produces zero duplicate rows
4. Integration test: `matching.engine` runs unmodified against Dodo-sourced fixture files and
   produces a valid `match_outcomes.csv`
5. Missing-key test: `DODO_API_KEY` unset + `--source dodo` exits nonzero with a clear
   message; synthetic path unaffected

**Laws:** L3, L18, L19, L20
**Stop.**

---

### CARD W12 — OpenAI resolution-learning rationale assist
**Status:** merged (PR #10, `7101fcf`, squash-merged by orchestrator per standing human
authorization). Verified before merge: suggestions restricted to predicate.py's existing
vocabulary only (L11), manual_one_off/no_pattern refused before any LLM call (L13),
`promote --confirm yes-always` completely untouched and still the only path to `rules.json`
(a suggestion only ever lands in `rule_candidates.json`). Full suite 345 passed at merge.
**Depends:** W8 merged (`llm_client.py`'s interface is fixed by W8, not W9 — no need to wait
for W9; runs in parallel with W9/W11/W13, mutually disjoint files)
**Branch:** `w12-openai-rationale`
**Reads:** `LEDGER-SENSE-v2-PRD.md`, `config.py` + `llm_client.py`, `learning/resolution.py`
(schema, read only), `learning/predicate.py` (vocabulary, read only)
**Writes / may touch:**
- `src/ledger_sense/learning/llm_rationale.py` (new)
- `tests/test_llm_rationale.py`
- A small, documented edit to `learning/cli.py`'s `resolve` command: when
  `config.openai_enabled()` and no explicit `--evidence` was given, offer this module's
  suggestion, clearly labeled SUGGESTION — the human's own `promote --confirm yes-always` step
  is not touched.

**Must implement:**
- Takes a `Resolution`'s `resolution_type` + free-text rationale, asks OpenAI (via
  `llm_client.py`) to suggest a candidate predicate in `learning/predicate.py`'s *existing*
  vocabulary — never a new one.
- `manual_one_off`/`no_pattern` never receive a suggestion (unchanged v1 rule, law L13).
- `OPENAI_API_KEY` absent → `ledger_sense resolve` behaves byte-identical to v1 (fully manual
  entry only).

**Does not:** touch the `promote`/`apply-rules` code path; touch matching/routing/guardrail;
auto-promote anything.

**Acceptance:**
1. Unit tests against a mocked OpenAI client
2. Regression: key unset → `ledger_sense resolve` output byte-identical to v1's
3. Test proving `promote --confirm yes-always` remains the only path that writes `rules.json`,
   and a suggested-but-unconfirmed predicate never appears there
4. `manual_one_off`/`no_pattern` resolutions never receive or use a suggestion

**Laws:** L11, L13, L14, L18, L20, L21, L22
**Stop.**

---

### CARD W13 — OpenAI routing fallback classifier
**Status:** merged (PR #12, `558cf41`, squash-merged by orchestrator per standing human
authorization). Verified before merge -- this is the card most likely to erode L21, checked
carefully: `apply_llm_fallback` re-checks the rule-7 marker itself (defense in depth beyond
`engine.py`'s own guard); a 5-row fixture with the fallback mocked-on shows exactly one LLM
call, on the rule-7 row, rules 1-4 byte-identical to `classify_bank`. Guardrail's independence
verified both structurally (doesn't even take `exceptions.csv` as input) and empirically
(release/audit/held CSVs byte-identical whether or not a row was LLM-relabeled, while that row
still gets a real, meaningful `dual_control` hold). 16 dedicated tests + full suite 346 passed.
**Depends:** W8 merged (`llm_client.py`'s interface is fixed by W8, not W9 — no need to wait
for W9; runs in parallel with W9/W11/W12, mutually disjoint files)
**Branch:** `w13-openai-routing-fallback`
**Reads:** `LEDGER-SENSE-v2-PRD.md`, `config.py` + `llm_client.py`, `routing/classify.py`
(existing classifier, read only)
**Writes / may touch:**
- `src/ledger_sense/routing/llm_classifier.py` (new)
- `tests/test_llm_classifier.py`
- A small, documented edit to wherever `classify_bank`'s result is consumed in
  `routing/engine.py`: call the LLM fallback ONLY when `classify_bank` would have returned via
  rule 7 (`unidentified_counterpart`, "no earlier condition matched") AND
  `config.openai_enabled()`. Rules 1–6 are completely untouched.

**Must implement:**
- Classifies into the same fixed 5-category taxonomy with a confidence score — never a 6th
  category.
- Every LLM-classified row is tagged/auditable in `exceptions.csv` (check the actual existing
  schema for the right field — don't invent a new column if an existing one fits).
- The guardrail's independent re-check is completely unaffected — it never trusts the
  classification source, only the row's own facts (law L21).
- `OPENAI_API_KEY` absent → rule 7's fallback behaves byte-identical to v1.

**Does not:** touch guardrail/**, matching/**, learning/**; touch rules 1–6 of `classify_bank`;
add a 6th category.

**Acceptance:**
1. Unit tests against a mocked OpenAI client
2. Regression: key unset → routing output byte-identical to v1
3. Test proving rules 1–6 are never intercepted or altered — only rule-7 cases reach the LLM
4. Test proving every LLM-classified row is tagged/auditable
5. Test proving guardrail's verdict for an LLM-classified row comes from guardrail's own
   unchanged independent logic, not passed through from the classifier

**Laws:** L1, L2, L18, L20, L21, L22
**Stop.**

---

### CARD W14 — v2 ship: metrics v2 + docs + live smoke test
**Status:** todo
**Depends:** W9 AND W10 AND W11 AND W12 AND W13 merged
**Branch:** `w14-v2-ship`
**Reads:** everything above, v1's `README.md`/`DEMO.md`/`BOARD.md`
**Writes / may touch:**
- `src/ledger_sense/metrics/**` (additive v2 fields only)
- `tests/test_metrics.py` (additive)
- `README.md`, `DEMO.md`, `BOARD.md` (v2 status section)

**Must implement:**
- Scoreboard v2 additive fields: total OpenAI cost this run; cost per STR point gained
  specifically attributable to the real adjudicator (compare a real-adjudicator run vs. a stub
  run on the same batch); latency delta (stub+synthetic vs. full live mode); Neatlogs
  trace-coverage percentage.
- README v2 section: how to enable each live-mode extra (`pip install
  ledger-sense[llm,dodo,tracing]`, the four env vars), a live-mode walkthrough alongside the
  unchanged v1 deterministic walkthrough.
- DEMO.md v2: an optional live-mode demo scene, clearly marked optional/requires-real-keys,
  alongside the unchanged v1 demo script.
- One true end-to-end live-mode smoke test, run manually with real API keys if the human has
  set them (locked decision 4) — document actual output (cost $, latency, trace count) in the
  PR description as evidence, not an assertion. If keys aren't set at build time, this becomes
  a clearly-flagged manual follow-up for the human, never silently skipped or asserted.
- Full offline pytest suite stays 100% green with zero live calls.

**Does not:** new architecture, new agents, DB/API-service migration, UI.

**Acceptance:**
1. Full offline suite green (v1's 277 + all new v2 tests)
2. Live-mode smoke test run with real documented output, or clearly flagged as a pending
   manual step if keys weren't available
3. BOARD.md updated with v2 status for all cards W8–W14
4. README/DEMO.md updated, no oversell (same discipline as v1's W7)

**Laws:** all of L1–L22
**Stop.**
