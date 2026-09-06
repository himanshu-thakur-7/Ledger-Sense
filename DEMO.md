# Ledger Sense — 60–90s demo script

Literal terminal commands, in order, from a clean checkout. No web UI, no mockups — the terminal
output *is* the demo. Every command below was actually run to produce the numbers shown; expect
byte-identical output on any machine (law L4). Total compute is well under a minute — most of a
60–90s take is narration between paste-and-enter.

```bash
pip install -e .
mkdir -p data
```

## 1. Chaos batch — pass 1 runs cold

```bash
python -m ledger_sense.data --seed 42 --pass-number 1 --n-cases 25000 --out-dir data/pass1 --overlay
```
```
Ledger Sense synthetic generation summary
  seed=42 pass_number=1 n_cases=25000
  row counts: ledger.csv=24500 bank.csv=27250 match_links.csv=26750
  unique counterparties: 800
  ...
  overlay: class='fee_offset' not planted (natural cluster already qualified) -- siblings=0 (natural max cluster observed=8, threshold=8)
```

```bash
python -m ledger_sense.matching --ledger data/pass1/ledger.csv --bank data/pass1/bank.csv --out-dir data/pass1
```
```
bank lines=27250; ledger entries=24500; matched=24336
cheap-tier match rate: 83.93% (22872/27250)
llm_is_stub=True; llm_calls=0; adjudicator=deterministic-stub-v1
```

```bash
python -m ledger_sense.routing --outcomes data/pass1/match_outcomes.csv \
  --settlements data/pass1/ledger_settlements.csv --ledger data/pass1/ledger.csv \
  --bank data/pass1/bank.csv --as-of 2026-07-01T00:00:00Z --out-dir data/pass1
```
```
exceptions=4088; owners=11; breached=4088
by category: {'duplicate': 1250, 'suspect_posting': 1316, 'unidentified_counterpart': 264, 'timing': 758, 'amount_mismatch': 500}
```

```bash
python -m ledger_sense.guardrail --ledger data/pass1/ledger.csv --bank data/pass1/bank.csv \
  --outcomes data/pass1/match_outcomes.csv --settlements data/pass1/ledger_settlements.csv \
  --as-of 2026-07-01T00:00:00Z --period-start 2025-12-01T00:00:00Z \
  --period-end 2026-07-01T00:00:00Z --out-dir data/pass1
```
```
bank lines=27250; policy_version=2026.09-1
allow: 24684/27250 (90.58%)
block: 2377/27250 (8.72%)
hold: 189/27250 (0.69%)
```

**4,088 real exceptions, cold.** Every one of them is now somebody's SLA clock.

## 2. Structured teach — a human resolves one, in real fields

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
```
resolution_id=RES-39f220941fae9aa9
exception_id=EXC-PAIR-BK-P1-002463-LG-P1-002214
resolution_type=reference_transform
rule_id=RULE-f5587af7405c
candidate predicate: amount_class=exact AND reference=wrong
support count against current exception pile: 87
status=candidate
```

No "approve" button — `--resolution-type`, `--reference-transform`, `--amount-class` are enum
fields in the matcher's own feature vocabulary. The tool prints the candidate rule **in plain
English** (`amount_class=exact AND reference=wrong`) plus its support count (**87** other open
exceptions this predicate already covers) before anyone commits to anything.

## 3. Promote — explicit "yes, always"

```bash
ledger_sense promote RULE-f5587af7405c --confirm yes-always \
  --promoted-by himanshu --promoted-at 2026-07-01T00:05:00Z \
  --rules data/rules.json --candidates data/candidates.json
```
```
RULE-f5587af7405c <- RES-39f220941fae9aa9
```

`rules.json` now exists. No other command path writes it.

## 4. Pass 2 — a genuinely new draw, rule applied before routing

```bash
python -m ledger_sense.data --seed 42 --pass-number 2 --n-cases 25000 --out-dir data/pass2 --overlay
```
```
  row counts: ledger.csv=24513 bank.csv=27263 match_links.csv=26763
  unique counterparties: 800
  ...
  overlay: class='fee_offset' PLANTED -- siblings=13 (natural max cluster observed=7, threshold=8)
```

```bash
python -m ledger_sense.matching --ledger data/pass2/ledger.csv --bank data/pass2/bank.csv --out-dir data/pass2
```
```
bank lines=27263; ledger entries=24513; matched=24360
cheap-tier match rate: 83.91% (22876/27263)
llm_is_stub=True; llm_calls=0; adjudicator=deterministic-stub-v1
```

Same shape as pass 1 (83.91% vs 83.93% cheap-tier) — pass 2 is not an easier batch.

```bash
ledger_sense apply-rules \
  --outcomes data/pass2/match_outcomes.csv --settlements data/pass2/ledger_settlements.csv \
  --ledger data/pass2/ledger.csv --bank data/pass2/bank.csv --rules data/rules.json \
  --as-of 2026-07-01T00:00:00Z --period-start 2025-12-01T00:00:00Z \
  --period-end 2026-07-01T00:00:00Z --out-dir data/pass2
```
```
rules loaded: 1
escalated lines seen: 1652
escalated lines matching a rule's predicate: 75
vetoed by guardrail (would_block_or_hold != allow): 0
predicate hit but no ledger capacity remained: 0
resolved by rule: 75
  RULE-f5587af7405c: 75 lines resolved
```

**This is the moment:** 75 lines that would otherwise have escalated straight into someone's SLA
queue never get there — resolved before routing ever sees them.

```bash
python -m ledger_sense.routing --outcomes data/pass2/match_outcomes.csv \
  --settlements data/pass2/ledger_settlements.csv --ledger data/pass2/ledger.csv \
  --bank data/pass2/bank.csv --as-of 2026-07-01T00:00:00Z --out-dir data/pass2

python -m ledger_sense.guardrail --ledger data/pass2/ledger.csv --bank data/pass2/bank.csv \
  --outcomes data/pass2/match_outcomes.csv --settlements data/pass2/ledger_settlements.csv \
  --as-of 2026-07-01T00:00:00Z --period-start 2025-12-01T00:00:00Z \
  --period-end 2026-07-01T00:00:00Z --out-dir data/pass2
```
```
exceptions=4039; owners=11; breached=4039
...
allow: 24715/27263 (90.65%)
block: 2367/27263 (8.68%)
hold: 181/27263 (0.66%)
```

## 5. Scoreboard — side-by-side, plus the trace

```bash
ledger_sense-scoreboard scoreboard --pass1-dir data/pass1 --pass2-dir data/pass2 \
  --rules data/rules.json --out data/scoreboard.json
```
```
-- Pass 1 --
  STR (naive, matched+settled): 23846/27250 (87.51%)
  STR (real, vs match_links.csv): 23846/27250 (87.51%)
  Exceptions remaining: 4088

-- Pass 2 --
  STR (naive, matched+settled): 23897/27263 (87.65%)
  STR (real, vs match_links.csv): 23895/27263 (87.65%)
  Exceptions remaining: 4039
  Rule-driven auto-resolves: 75 (trace coverage 100.00%)

Learned rule count: 1

-- Exception classes (counterparty | amount-bucket | reference-pattern) --
  924 class(es) eliminated (pass1 > 0, pass2 == 0):
    ...
```

STR climbs pass 1 → pass 2 (87.51% → 87.65%), and every one of the 75 rule-driven resolves traces
to `rule_id=RULE-f5587af7405c` ← `resolution_id=RES-39f220941fae9aa9` at 100% coverage — open
`data/scoreboard.json`'s `rule_trace` array to see each `bank_txn_id`/`ledger_id` pair named
individually, not just counted. Of the 924 exception classes that disappear between the two
passes, 84 share the promoted rule's exact shape; the other 840 are ordinary two-draw variance,
reported separately rather than folded into one bigger number.

**That's the whole demo.** One human decision, made once, in structured fields — resolved before
it ever became someone else's SLA problem the second time it recurred.

---

## 6. Optional — v2 live-mode scene (requires real API keys; skip if you don't have them)

Everything above is v1: zero external calls, zero API spend, fully reproducible from a clean
clone. This scene is **optional** — it only runs if `OPENAI_API_KEY` (and, for the tracing/Dodo
lines, `NEATLOGS_API_KEY`/`DODO_API_KEY`) are actually configured; omit any of them and the exact
same commands below fall back to v1's stub/manual/deterministic/no-tracing behavior automatically
(law L18), never crash.

```bash
pip install -e ".[llm,dodo,tracing]"
```

```bash
python -m ledger_sense.matching --ledger data/pass1/ledger.csv --bank data/pass1/bank.csv \
  --out-dir data/pass1 --adjudicator auto
```
```
bank lines=327; ledger entries=294; matched=276
cheap-tier match rate: 84.10% (275/327)
llm_is_stub=False; llm_calls=36; adjudicator=gpt-4o-mini
```

**Real OpenAI, real dollars, real bounded fallback** — 36 gray-zone candidates got a real answer
from `gpt-4o-mini`; the rest of the batch (of 51 escalated) fell back to the same deterministic
stub rather than blocking, exactly per the seam's contract. `ledger_sense-scoreboard`'s new v2
flags turn that into a CFO-relevant number, real cost included:

```bash
ledger_sense-scoreboard scoreboard --pass1-dir data/pass1 --pass2-dir data/pass2 \
  --rules data/rules.json --out data/scoreboard.json \
  --llm-cost-usd 0.002971 \
  --adjudicator-stub-dir data/pass1-stub --adjudicator-llm-dir data/pass1-live \
  --stub-duration-seconds 9.876 --live-duration-seconds 83.620 \
  --entrypoints-run 4 --spans-emitted 0
```
```
-- v2 (live-mode) --
  OpenAI cost this run: $0.002971
  Real adjudicator STR lift: 288 -> 271 (-17 points); cost/point: n/a (no STR gain measured)
  Latency delta (stub+synthetic vs. live): 9.876s -> 83.620s (delta 73.744s)
  Neatlogs trace coverage: 0/4 (0.00%)
```

**Said plainly, not oversold:** in the actual smoke-test batch behind these numbers (`seed=777,
n-cases=300` — a different, smaller batch than sections 1–5 above, chosen to keep real OpenAI
spend and wall-clock small), the real adjudicator resolved *fewer* net straight-through rows than
the plain stub heuristic, so `cost/point` is honestly `n/a` rather than a fabricated figure —
ground-truth precision stayed 100.00% on both sides, only raw match count differed. Neatlogs spans
never actually reached the real service this run (the installed `neatlogs` package doesn't expose
the `Client` class `tracing.py` calls — a real, disclosed integration gap, not a silent skip); a
real Dodo Payments sandbox pull (`python -m ledger_sense.data --source dodo`) returned `HTTP 403
Forbidden` rather than data. Full derivation of every number above — tokens, retries, the exact
failure text — is in the W14 PR description.

**This is the honest version of the live-mode demo:** real API keys, a real dollar spent, a real
partial integration failure disclosed rather than hidden, and the guardrail/matcher's deterministic
core completely unmoved by any of it.
