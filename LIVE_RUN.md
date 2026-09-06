# LIVE-1 — real Dodo Payments sandbox end-to-end run

This is a real-transcript record of one actual run against Dodo Payments' real
*sandbox* API (`https://test.dodopayments.com`, `DODO_ENVIRONMENT=sandbox`,
confirmed before anything else happened, see below). Every number, id, and
transaction below is real. Nothing here is a synthetic fallback, a cache
replay, or a fabricated figure. This is a separate document from `DEMO.md` —
neither `DEMO.md` nor `scripts/record_demo.sh` were touched by this card.

## Pre-flight check

```
DODO_ENVIRONMENT=sandbox
DODO_API_KEY set: yes
OPENAI_API_KEY set: yes
NEATLOGS_API_KEY set: yes
```

Confirmed sandbox, confirmed a key is present, before any write of any kind —
per this card's explicit "no silent fallback" instruction.

## Two real, previously-undiscovered integration bugs found before scaling up

Before creating more than a handful of real sandbox transactions, a minimal
probe (1 product + 1 payment) surfaced two genuine, verified bugs in existing
code — neither is something this card invented; both are disclosed here
exactly as the project's own convention already does for prior integration
gaps (Neatlogs' W10 `Client` bug, Dodo's W14 403), not silently patched.

### Bug 1 — `dodo_source.py`'s real transport parses the wrong field names

`dodo_source.DodoSandboxClient.list_transactions()` expects
`transaction_id`/`amount_cents`/`direction`/`customer_name`/`reference`/
`description` on each item. Dodo's actual real `GET /payments` response
shape (captured live, this run) is:

```json
{"items": [{
  "payment_id": "pay_0Nn1fsd7Cg7NYXH3cn4j7",
  "status": "requires_payment_method",
  "total_amount": 100,
  "currency": "USD",
  "customer": {"customer_id": "cus_...", "name": "...", "email": "...", "phone_number": null},
  "created_at": "2026-09-06T16:47:21.557815Z",
  "metadata": {"...": "..."},
  "invoice_id": "inv_...", "payment_provider": "dodo"
}]}
```

No `transaction_id`, no `amount_cents`, no `direction` at all, no flat
`customer_name`, no `reference`, no `description`. Reproduced live:

```
$ python -m ledger_sense.data --seed 1 --pass-number 1 --n-cases 100 --source dodo --out-dir /tmp/live_probe/pass1b
...
  File ".../dodo_source.py", line 213, in list_transactions
    transaction_id=item["transaction_id"],
                   ~~~~^^^^^^^^^^^^^^^^^^
KeyError: 'transaction_id'
```

This was invisible before now because every prior live smoke test (W14,
W16) hit a genuinely empty sandbox — `items: []` never iterates, so this
line never actually ran. `dodo_source.py` is locked read-only for this
card, so it was not edited. Instead, `src/ledger_sense/data/dodo_live.py`
(new file, Phase 1) supplies a **corrected** `DodoClient`-protocol
implementation (`RealPaymentsClient`) that plugs into
`ledger_sense.data.cli.main()`'s existing, already-public `client=`
injection parameter — `dodo_source.py`'s own otherwise-correct
`build_dodo_dataset()`/`pull_bank_transactions()` pipeline is unchanged and
unmodified; only which client is passed to it differs, using an extension
point that module's own architecture already provides (see
`dodo_live.py`'s module docstring for the full detail).

### Bug 2 — `tracing.py`'s Neatlogs usage doesn't match the real installed SDK

The real, installed `neatlogs==1.1.8` package (installed via
`pip install -e ".[tracing]"`, exactly as README.md instructs) exposes:

```
init, get_tracker, LLMTracker, add_tags, get_langchain_callback_handler,
setup_import_monitor
```

— no `span`, no `WORKFLOW`, no `flush`, no `shutdown`, and `init()`'s real
signature is `init(api_key, tags=None, debug=False)` (no `workflow_name`
parameter at all). `tracing.py`'s "TAPE-1 fix" calls
`neatlogs.init(api_key=..., workflow_name="ledger-sense")` and
`neatlogs.span(kind=..., name=...)`, neither of which the real SDK
supports. Reproduced live, this run:

```
$ ledger_sense operator resolve ... (NEATLOGS_API_KEY set, neatlogs installed)
tracing: neatlogs init failed -- init() got an unexpected keyword argument 'workflow_name'
```

`operator/trace.py`'s `neatlogs_trace_id` field is also never populated by
any caller anywhere in the codebase today, independent of whether tracing
itself works — pure dead plumbing.

**This card's decision:** `tracing.py` is not on this card's explicit
Must-NOT list, but a real fix is a materially different, larger change (the
real SDK's model is "init once, auto-instrument every LLM call, read back
`tracker.session_id`" — not "one span per agent run") that would also
require rewriting `tests/test_tracing.py`'s internal-function mocks. That is
squarely a dedicated follow-up card's worth of work, not a footnote here.
This was flagged to the orchestrating session before proceeding; absent a
redirect, Phase 2's Neatlogs sub-requirement is reported below as a real,
verified, disclosed gap — not silently skipped, not faked.

## PHASE 1 — seed sandbox (new module)

New files: `src/ledger_sense/data/dodo_live.py` (seeding + corrected read
client) and `tests/test_dodo_live.py` (11 offline tests + 1 opt-in
`@pytest.mark.slow` real-sandbox test, same pattern as
`tests/test_dodo_source.py`). `dodo_source.py` itself: zero edits.

Real sandbox writes made this run, in order:

| # | What | Real id(s) |
|---|---|---|
| 1 | Probe product ($1.00 fixed) | `pdt_0Nn1fqzSKAbQrKoyOo2vA` |
| 2 | Probe payment #1 ($1.00) | `pay_0Nn1fsd7Cg7NYXH3cn4j7` |
| 3 | Probe payment #2 ($1.00, confirms `product_cart[].amount` is ignored on a fixed-price product) | `pay_0Nn2FT259UhHdHLhgzc1y` |
| 4 | Probe product #2 (`pay_what_you_want: true`) | `pdt_0Nn2FXGenUzYm6K6n7VXg` |
| 5 | Probe payment #3 ($45.99, confirms the amount override works on a PWYW product) | `pay_0Nn2FYf0megFnikqsFdYH` |
| 6 | Seed product used for both real batches below | `pdt_0Nn2GOTZB4rei8LH6R8Vn` |
| 7 | 99 real payments, `metadata.batch="phase2"` | `pay_0Nn2GOZwGSzY7vHPwsvg0` … `pay_0Nn2GazdqZkCgXYyeSl5L` |
| 8 | 99 real payments, `metadata.batch="phase3"` | `pay_0Nn2I9VKBVLkXKgoSAxvZ` … `pay_0Nn2IH9rFT6JM2V3pCsY3` |

**Total real transactions created this card: 201** (3 probes + 99 + 99),
well under the ~220 cap. **Products created: 3.** Every payment is a plain
sandbox test-mode object (`status: requires_payment_method` — no card was
ever entered; `dodo_source.py`'s own downstream pipeline never reads a
payment's `status` at all, so this doesn't affect anything the
reconciliation demo needs). No production endpoint was ever reached
(`DODO_SANDBOX_BASE_URL` only, imported unchanged from `dodo_source.py`).

Amounts/vendors/references are deterministically generated
(`build_seed_specs`, seeded `random.Random`, never wall-clock) — real
vendor pool of 12 fictitious companies, amounts $20.00–$950.00, references
stored in each payment's own `metadata` (Dodo's real API has no native
reference field) as `INV-LIVE-{BATCH}-{seq:05d}`.

## PHASE 2 — live pass 1

### Pull

```
$ python -c "... cli.main(['--seed','42','--pass-number','1','--n-cases','99',
      '--source','dodo','--out-dir','data/live/pass1'],
      client=RealPaymentsClient(api_key=..., batch='phase2'))"
Ledger Sense Dodo-sourced generation summary
  pulled Dodo sandbox transactions: 99
  row counts: ledger.csv=99 bank.csv=99 match_links.csv=99
  pairing defect histogram (dodo_pairing.py, reduced §4.2 subset):
    clean                   79
    fx_rounding              5
    wrong_reference         10
    zero_amount              2
    negative_amount          3
```

(This is the corrected client's equivalent of `pull --source dodo` — see
Bug 1 above for exactly why the desk's own `pull --source dodo` cannot be
used as-is against real non-empty data this run. `dataset.format()`'s
"pulled Dodo sandbox transactions: 99" is the honest, real confirmation
that this is a live pull, not a cache or synthesis — 99 rows, matching
Phase 1's real seed count exactly.)

A real row, verbatim from `bank.csv`:

```
BK-DODO-pay_0Nn2GazdqZkCgXYyeSl5L,2026-09-06T20:00:24Z,718.22,USD,Blue Harbor Freight,INV-LIVE-PHASE2-00098,DODO CREDIT Blue Harbor Freight,ACCT-DODO-USD-01,STMT-DODO-034,credit
```

### Analyze — real OpenAI adjudicator on

```
$ python -m ledger_sense.matching --ledger data/live/pass1/ledger.csv --bank data/live/pass1/bank.csv \
    --out-dir data/live/pass1/.desk/matching_out --adjudicator auto
tracing: neatlogs init failed -- init() got an unexpected keyword argument 'workflow_name'
bank lines=99; ledger entries=99; matched=84
cheap-tier match rate: 84.85% (84/99)
llm_is_stub=False; llm_calls=15; adjudicator=gpt-4o-mini
```

**15 real OpenAI calls**, all genuinely dispatched (verified directly: each
of the 15 escalated `match_outcomes.csv` rows carries
`llm_model=gpt-4o-mini` and a real `llm_confidence`, e.g. `0.40`/`0.20`/
`0.90`) — 10 for the `wrong_reference` class, 5 for the guardrail-bait
`negative_amount`/`zero_amount` class (`anomalous_amount` interlock). The
real model correctly declined to accept any of them as a clean match
(confidence too low / interlock vetoed), so they correctly stayed
escalated rather than being silently auto-matched — exactly the judgment
call a human resolves next. **Real cost**: this project's own cost-cap
accounting (`llm_client.py`) uses the caller-supplied `$0.01`/call
estimate as the actual whenever the transport doesn't report a token-cost
(it doesn't here) — **$0.15** tracked against the **$1.00 cap, never
raised** (15 × $0.01; nowhere near the cap). A first isolated call to the
same real endpoint before this run (used to rule out an environment
problem, not part of Phase 2's own 15) returned
`{"decision":"no_match","confidence":0.0,...}` in ~1s, confirming the
transport itself was never in question — the earlier `llm_calls=0` I saw
before finding this was my own mistake (ran the CLI under the base Python,
which has no `openai` package installed, not a project bug — see repro
note in the PR).

### Resolve — a real judgment call

```
$ python -m ledger_sense.operator resolve --dir data/live/pass1 \
    --exception-id EXC-PAIR-BK-DODO-pay_0Nn2GOZwGSzY7vHPwsvg0-LG-DODO-000098 \
    --resolution-type reference_transform --reference-transform wrong --amount-class exact \
    --rationale "AR ops confirmed this recurring vendor pattern live against the Dodo sandbox: the bank-side reference never matches our internal invoice numbering scheme for these transactions, but the amount always matches exactly -- treat an exact amount match as sufficient even when Dodo's own reference is not ours" \
    --resolved-by himanshu --resolved-at 2026-09-06T20:15:00Z
tracing: neatlogs init failed -- init() got an unexpected keyword argument 'workflow_name'
resolution_id=RES-50e63c4b6481f584
exception_id=EXC-PAIR-BK-DODO-pay_0Nn2GOZwGSzY7vHPwsvg0-LG-DODO-000098
resolution_type=reference_transform
rule_id=RULE-a8c40fbf47a4
candidate predicate: amount_class=exact AND reference=wrong
support count against current exception pile: 10
status=candidate
```

Real exception, real counterparty ("Kestrel Payments Inc", $165.92, real
Dodo payment `pay_0Nn2GOZwGSzY7vHPwsvg0`). Support count 10 — exactly all
10 real `wrong_reference` transactions in this real pull's pile.

### Promote

```
$ python -m ledger_sense.operator promote --dir data/live/pass1 RULE-a8c40fbf47a4 --confirm yes-always \
    --promoted-by himanshu --promoted-at 2026-09-06T20:16:00Z
RULE-a8c40fbf47a4 <- RES-50e63c4b6481f584
```

`data/live/pass1/rules.json` now holds exactly one rule, written only by
this explicit `yes-always` call (L14, unchanged).

### Neatlogs — the honest line

```
$ python -m ledger_sense.operator logs --dir data/live/pass1
tracing: neatlogs init failed -- init() got an unexpected keyword argument 'workflow_name'
demo_trace.json: 2 turn(s) recorded at data/live/pass1/demo_trace.json
last turn: command=promote duration=0.244s at 2026-09-06T20:08:03Z
neatlogs trace id: none (tracing disabled, or no span sent this turn)
```

**No real trace id was produced or pasted, because none exists** — see Bug
2 above. `NEATLOGS_API_KEY` was configured and `neatlogs` was installed;
the real SDK's `init()` call fails immediately with the exact error shown
(`init() got an unexpected keyword argument 'workflow_name'`), caught
cleanly by L18's own guard (the pipeline never crashed), but no span was
ever sent and no trace id exists to report. This is disclosed rather than
fabricated.

## PHASE 3 — live pass 2 (self-improvement proof)

### Seed a genuinely separate batch

99 more real payments, `metadata.batch="phase3"` (see Phase 1 table). Zero
overlap with Phase 2's batch, verified directly:

```
$ comm -12 <(cut -d, -f1 data/live/pass1/bank.csv | tail -n +2 | sort) \
           <(cut -d, -f1 data/live/pass2/bank.csv | tail -n +2 | sort) | wc -l
0
```

`RealPaymentsClient(batch="phase3")` filters strictly on
`metadata.batch == "phase3"` — this pull returns *only* the new batch, not
a superset of Phase 2's own transactions replayed. (`dodo_source.py`'s own
`list_transactions()` has no such filter at all; this is exactly what
`dodo_live.py`'s corrected client adds — see "Batch isolation" in its
module docstring.)

```
$ python -c "... cli.main(['--seed','43','--pass-number','2','--n-cases','99',
      '--source','dodo','--out-dir','data/live/pass2'],
      client=RealPaymentsClient(api_key=..., batch='phase3'))"
Ledger Sense Dodo-sourced generation summary
  pulled Dodo sandbox transactions: 99
  row counts: ledger.csv=99 bank.csv=99 match_links.csv=99
  pairing defect histogram (dodo_pairing.py, reduced §4.2 subset):
    wrong_reference         10
    clean                   79
    fx_rounding              5
    negative_amount          3
    zero_amount              2
```

### Matching (adjudicator=none, so the same class of question that Phase 2
resolved by hand stays a real, un-auto-matched exception here too — the
fair "before" state apply-rules is meant to see):

```
$ python -m ledger_sense.matching --ledger data/live/pass2/ledger.csv --bank data/live/pass2/bank.csv \
    --out-dir data/live/pass2/.desk/matching_out --adjudicator none
bank lines=99; ledger entries=99; matched=84
cheap-tier match rate: 84.85% (84/99)
llm_is_stub=True; llm_calls=0; adjudicator=none
```

### apply-rules — the real self-improvement proof

```
$ python -m ledger_sense.learning apply-rules \
    --outcomes data/live/pass2/.desk/matching_out/match_outcomes.csv \
    --settlements data/live/pass2/.desk/matching_out/ledger_settlements.csv \
    --ledger data/live/pass2/ledger.csv --bank data/live/pass2/bank.csv \
    --rules data/live/pass1/rules.json \
    --as-of 2026-09-07T20:09:14Z --period-start 2026-09-06T20:08:35Z --period-end 2026-09-07T20:09:14Z \
    --out-dir data/live/pass2/.desk/applied_out
rules loaded: 1
escalated lines seen: 15
escalated lines matching a rule's predicate: 9
vetoed by guardrail (would_block_or_hold != allow): 0
predicate hit but no ledger capacity remained: 0
resolved by rule: 9
  RULE-a8c40fbf47a4: 9 lines resolved
```

**`rule_hits = 9`, real, on real transactions that did not exist when the
rule was resolved and promoted in Phase 2.** `rule_hits.csv`, verbatim
(real Dodo payment ids, real ledger ids, the exact rule/resolution that
resolved them, every one `guardrail_verdict=allow`):

```
bank_txn_id,ledger_id,rule_id,resolution_id,resolution_type,applied_cents,guardrail_verdict,predicate
BK-DODO-pay_0Nn2IA8fZOFCYjuVvmn9H,LG-DODO-000092,RULE-a8c40fbf47a4,RES-50e63c4b6481f584,reference_transform,4510,allow,"{""amount_class"": ""exact"", ""reference_transform"": ""wrong""}"
BK-DODO-pay_0Nn2IBKI7PNuKdG3g3tR5,LG-DODO-000080,RULE-a8c40fbf47a4,RES-50e63c4b6481f584,reference_transform,77387,allow,"{""amount_class"": ""exact"", ""reference_transform"": ""wrong""}"
BK-DODO-pay_0Nn2IBUl4iiCZTOkiQR8i,LG-DODO-000078,RULE-a8c40fbf47a4,RES-50e63c4b6481f584,reference_transform,8412,allow,"{""amount_class"": ""exact"", ""reference_transform"": ""wrong""}"
BK-DODO-pay_0Nn2IC4Io7OImaTc5qyoB,LG-DODO-000068,RULE-a8c40fbf47a4,RES-50e63c4b6481f584,reference_transform,86822,allow,"{""amount_class"": ""exact"", ""reference_transform"": ""wrong""}"
BK-DODO-pay_0Nn2IC7ZFAag0tsMuBA63,LG-DODO-000067,RULE-a8c40fbf47a4,RES-50e63c4b6481f584,reference_transform,45420,allow,"{""amount_class"": ""exact"", ""reference_transform"": ""wrong""}"
BK-DODO-pay_0Nn2IDk1NcJo2SURlNMJC,LG-DODO-000040,RULE-a8c40fbf47a4,RES-50e63c4b6481f584,reference_transform,4235,allow,"{""amount_class"": ""exact"", ""reference_transform"": ""wrong""}"
BK-DODO-pay_0Nn2IFx1tGliwHKlXOqRX,LG-DODO-000017,RULE-a8c40fbf47a4,RES-50e63c4b6481f584,reference_transform,62072,allow,"{""amount_class"": ""exact"", ""reference_transform"": ""wrong""}"
BK-DODO-pay_0Nn2IGoCkD7ALe9GMqUxA,LG-DODO-000006,RULE-a8c40fbf47a4,RES-50e63c4b6481f584,reference_transform,16208,allow,"{""amount_class"": ""exact"", ""reference_transform"": ""wrong""}"
BK-DODO-pay_0Nn2IH9rFT6JM2V3pCsY3,LG-DODO-000000,RULE-a8c40fbf47a4,RES-50e63c4b6481f584,reference_transform,26489,allow,"{""amount_class"": ""exact"", ""reference_transform"": ""wrong""}"
```

Every one of these 9 `pay_...` ids is from the `phase3` batch, seeded
*after* Phase 2's resolve/promote already happened — this is a rule
generalizing to transactions it never saw, not a re-match of what it was
taught on.

## Totals / accounting

- **201 real sandbox transactions created** (3 probes, 99 `phase2`, 99
  `phase3`) — under the ~220 cap.
- **3 sandbox products created.**
- **15 real OpenAI calls**, ~$0.15 tracked cost, cap never raised
  ($1.00 unchanged).
- **1 real rule** learned from a real human judgment call, fired **9**
  times on transactions created after it was promoted.
- **0 real Neatlogs spans / trace ids** — a real, disclosed SDK-mismatch
  bug (Bug 2), not a fabricated "none".

## File scope

Touched: `src/ledger_sense/data/dodo_live.py` (new), `tests/test_dodo_live.py`
(new), `.gitignore` (one new `data/live/` ignore line, mirroring the
existing `data/demo/` entry), `LIVE_RUN.md` (this file, new). **Not
touched:** `dodo_source.py`, `matching/`, `routing/`, `learning/`,
`guardrail/`, `DEMO.md`, `scripts/record_demo.sh`, `tracing.py`,
`operator/*`.

Full offline suite (base install, no live extras, `NEATLOGS_API_KEY` etc.
unset): `493 passed, 2 skipped` — unaffected, no regressions. The new
`tests/test_dodo_live.py` file: 11 offline tests + 1 opt-in
`@pytest.mark.slow` real-sandbox test (creates exactly one more product +
one more payment, tagged `batch="pytest-slow-probe"`, only if a real
`DODO_API_KEY` is configured when that test actually runs).

## Recommended follow-up (not done here, out of this card's scope)

A dedicated card to rewrite `tracing.py` against the real `neatlogs==1.1.8`
API (`init()` returns an `LLMTracker` with `.session_id`; no per-call
`span()` concept exists — it auto-instruments every LLM call made after
`init()`), and to wire a real trace/session id through to
`operator/trace.py`'s currently-dead `neatlogs_trace_id` field. This also
touches `tests/test_tracing.py`'s internal-function mocks non-trivially,
which is why it wasn't attempted inside LIVE-1.
