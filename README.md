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
of their own. Run `pytest` — measured here (v1's original **277 passed, 2 skipped**, plus v2's
W8–W14 additions, all against mocked transports, zero live calls, per law L20): **449 passed, 2
skipped**. The 2 skips need a local `MATCHING_BATCH_DIR`/real batch on disk, which a fresh clone
doesn't have; harmless. The `slow` marker (registered in `pyproject.toml`) covers the real-CLI
end-to-end pipeline tests — it runs by default; `pytest -m "not slow"` excludes it if you want the
fast subset only. Add `[llm,dodo,tracing]` to the `pip install -e .` above only if you intend to
exercise v2's optional live-mode integrations (see [v2 — real integrations](#v2--real-integrations-optional-live-mode-only)
below) — the base install and every test above stay exactly as dependency-free as v1 shipped.

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

## Recording

`bash scripts/record_demo.sh` drives the real interactive close desk (`desk> ` — see
[operator/](src/ledger_sense/operator)) end to end and prints exactly what a human recording the
demo would type and see — nothing pre-canned. It's the companion to `DEMO.md`, which carries the
~3-minute spoken script alongside the literal `desk>` transcript.

```bash
bash scripts/record_demo.sh
```

- **Small and keyless on purpose.** `DODO_API_KEY`/`OPENAI_API_KEY`/`NEATLOGS_API_KEY` are
  unset for the run, and `n<=400` throughout (`pull`=200, `next close`=300) — this is a fast
  recording aid, not another run of the 25,000-row pipeline measured above. It generates
  `data/demo/pass1` only if that directory is missing, and finishes in a couple of seconds when
  the data is already there.
- **Two `desk> ` chat sessions, not one.** `promote` needs the real `rule_id` a prior `resolve`
  just minted, and that can't be baked into a script's input ahead of time — so the recording
  captures it from the first session's own output and feeds it into the second.
- Ends with one raw, non-`desk>` call to `ledger_sense apply-rules` — the same call `next close`
  already makes internally, re-run standalone so its full receipt (`resolved by rule: N`)
  prints verbatim rather than folded into the desk's own summary line.

## v2 — real integrations (optional, live-mode only)

Everything above this section is v1, unchanged: zero external calls, zero API spend, the
cheap-tier deterministic matcher is the only thing that ever produced a number in this document.
v2 (`LEDGER-SENSE-v2-PRD.md`) adds three *optional* real integrations behind the exact seams v1
already left for them — it does not touch the matcher's scoring, the guardrail's policy, or
routing's deterministic rules 1–6. **Every one of them defaults off.** Omit every env var below
and the pipeline runs byte-identical to every number already printed in this README (law L18).

| Extra | Enables | Falls back to, if unconfigured |
|---|---|---|
| `llm` (`openai>=1.0`) | Real `OpenAIAdjudicator` for matching's gray-zone (`--adjudicator auto`); OpenAI-suggested predicate on `resolve`; OpenAI fallback classifier for routing's rule 7 | `StubAdjudicator` / fully manual predicate entry / deterministic rule-7 (v1, unchanged) |
| `dodo` (`httpx>=0.27`) | `LEDGER_SENSE_DATA_SOURCE=dodo` — real Dodo Payments *sandbox* transactions instead of synthesis | Synthetic generator (v1, unchanged, and still the default even with `dodo` requested if no key is set) |
| `tracing` (`neatlogs`) | A Neatlogs span per agent CLI entrypoint | No tracing at all — zero overhead, nothing imported |

### Enabling a live-mode extra

```bash
pip install -e ".[llm,dodo,tracing]"   # or any subset: ".[llm]", ".[llm,tracing]", ...
```

Then set the env vars that extra needs (copy `.env.example` to `.env`, or export them directly —
either way, real values never get committed; `.env` is git-ignored):

| Var | Used by | Default when unset |
|---|---|---|
| `OPENAI_API_KEY` | matching adjudicator, learning rationale assist, routing fallback | unset → all three stay v1 (stub/manual/deterministic) |
| `LEDGER_SENSE_OPENAI_MODEL` | same three | `gpt-4o-mini` |
| `LEDGER_SENSE_LLM_COST_CAP_USD` | same three, shared `llm_client.py` cap | `1.00` (USD, per full pipeline run) |
| `DODO_API_KEY` / `DODO_ENVIRONMENT` | `--source dodo` | unset → `--source dodo` still degrades to synthetic |
| `LEDGER_SENSE_DATA_SOURCE` | `python -m ledger_sense.data` | `synthetic` |
| `NEATLOGS_API_KEY` | every agent CLI entrypoint's tracing wrap point | unset → tracing is a complete no-op |

Nothing here is ever read from anywhere but `config.py` (`openai_enabled()`/`dodo_enabled()`/
`tracing_enabled()`); no other module touches `os.environ` directly (W8 design constraint).

### Live-mode walkthrough (parallel to the v1 walkthrough above)

Only the invocation changes — inputs, outputs, and every downstream agent are unchanged:

```bash
# Real gray-zone adjudication (falls back to the same StubAdjudicator on any API
# failure, cost-cap breach, or malformed response -- never crashes, never blocks):
python -m ledger_sense.matching --ledger data/pass1/ledger.csv --bank data/pass1/bank.csv \
  --out-dir data/pass1 --adjudicator auto

# routing/guardrail/learning need no extra flag -- OpenAI's rationale assist (learning)
# and rule-7 fallback (routing) activate automatically once OPENAI_API_KEY is configured,
# and degrade to v1's manual/deterministic behavior automatically if it isn't.
python -m ledger_sense.routing ...   # unchanged invocation
ledger_sense resolve ...             # unchanged invocation -- prints an extra
                                      # "SUGGESTION (gpt-4o-mini): ..." line when a key is set

# Real Dodo Payments sandbox data instead of synthesis:
python -m ledger_sense.data --seed 42 --pass-number 1 --n-cases 25000 --out-dir data/pass1 \
  --source dodo
```

### Scoreboard v2 fields

`ledger_sense-scoreboard scoreboard` gained four **additive**, all-optional flags
(`--llm-cost-usd`, `--adjudicator-stub-dir`/`--adjudicator-llm-dir`,
`--stub-duration-seconds`/`--live-duration-seconds`, `--entrypoints-run`/`--spans-emitted`). Omit
all of them and `scoreboard.json`'s new `"v2"` key reports every sub-metric `"measured": false` —
the terminal report prints nothing extra either, so a v1 caller sees byte-identical output. Each
figure is a *caller-supplied measurement of a run that already happened* (this agent still never
runs Agents 1–4 itself, computing "only from files/args already given," now extended to include
values the operator measured around a real run) — never fabricated, never estimated by
`scoreboard.py` itself:

- **Total OpenAI cost this run** — real $ spent, as read off the real adjudicator's
  `LLMClient.cumulative_cost_usd` (or computed from real token counts, see below).
- **Cost per STR point gained, attributable to the real adjudicator** — requires two full pass
  directories over the *identical* underlying batch, one produced with `--adjudicator stub`, one
  with `--adjudicator auto`. Reports the real, ground-truth-checked (`match_links.csv`)
  straight-through delta between them, and `$/point` only when that delta is positive — a
  zero-or-negative measured gain is reported as `cost_per_str_point_usd: null` ("no STR gain
  measured this run"), never divided-by-zero or asserted as free.
- **Latency delta** — stub+synthetic wall-clock vs. full live-mode wall-clock, both timed by the
  caller (`scoreboard.py` never times anything itself).
- **Neatlogs trace-coverage** — spans actually emitted ÷ entrypoints run, both counted by the
  caller; Neatlogs is a real external service, not a file this package can read.

### Live-mode smoke test — actual measured evidence (2026-09-06)

One real end-to-end run, both adjudicators against the *identical* synthetic batch
(`seed=777, n-cases=300` → 294 ledger rows / 327 bank rows), all four real keys configured
(`OPENAI_API_KEY`, `DODO_API_KEY`+`DODO_ENVIRONMENT=sandbox`, `NEATLOGS_API_KEY`), package
installed as `pip install -e ".[llm,dodo,tracing]"`. Full command trace and every derived number
below are in the W14 PR description; summarized here:

| Metric | Value |
|---|---:|
| Real OpenAI calls (matching adjudicator) | 36 |
| Real tokens (prompt / completion) | 14,126 / 1,420 |
| Real OpenAI cost (gpt-4o-mini list pricing: $0.15/$0.60 per 1M in/out tokens) | **$0.002971** |
| Match precision vs. ground truth, both adjudicators | 100.00% (no false positives either way) |
| STR (real) — stub vs. real adjudicator, same batch | 288 → 271 (**−17 points**) |
| Cost per STR point gained | **n/a — no gain measured this run** (real adjudicator resolved *fewer* net rows than the stub heuristic on this small batch; see PR body) |
| Wall-clock — stub+synthetic vs. live (matching+routing+guardrail) | 9.876s → 83.620s (**+73.744s**) |
| Neatlogs trace coverage | **0/4 (0%)** |
| Dodo sandbox pull | attempted, **HTTP 403 Forbidden** |

**Two integrations did not actually work against the real (not mocked) third-party services**,
found only by this live run, not by any mocked unit test:

- **Neatlogs**: the real installed `neatlogs` package (v1.1.8) exposes no `Client` class —
  `tracing.py`'s `_build_client()` (`neatlogs.Client(api_key=...)`) raises `AttributeError` every
  time. L18's broad exception guard swallows this correctly (every entrypoint still completes,
  exit 0, stdout unaffected) — the pipeline never crashed, but **zero spans were ever actually
  sent**. Confirmed both indirectly (all four entrypoints run, trace-coverage measured 0/4) and
  directly (calling `tracing._build_client()` + `.send()` once, by hand, reproduces the exact
  `AttributeError` above). `tracing.py` is out of this card's file scope (W14 may not touch it);
  this is disclosed as a real, observed integration gap for a future card, not silently patched
  over or hidden.
- **Dodo Payments**: `python -m ledger_sense.data --source dodo` with a real sandbox key
  configured reached `https://test.dodopayments.com/payments` and got back `HTTP 403 Forbidden`
  after 3 bounded retries (law L22 held — no infinite loop) — a real, specific, observed failure,
  not "no seeded sandbox transactions" (a 403 is an authorization/endpoint-shape response, not an
  empty-but-authorized 200). `dodo_source.py`'s assumed request shape may not match Dodo's actual
  sandbox API; also worth noting for a future card: `DodoAPIError` (a *configured* key that fails)
  isn't caught by `cli.py`'s top-level handler the way `DodoNotConfiguredError` (an *absent* key)
  is, so this exits with a full traceback rather than the clean one-line message a missing key
  gets — again disclosed, not fixed here (`data/` is out of this card's file scope).

Genuinely working this run: the real OpenAI matching adjudicator (dispatched real calls, real
tokens, real — if small — spend) and its L18/L22 fallback discipline: partway through the batch
36 of 51 gray-zone candidates got a real answered verdict before the remainder fell back to the
deterministic stub for that batch, exactly as designed — never a crash, never a block, and ground
truth precision stayed 100% on both sides throughout.

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
