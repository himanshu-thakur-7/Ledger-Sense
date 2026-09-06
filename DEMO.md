# Ledger Sense — close-desk demo (spoken script + literal transcript)

This is the recording script for the interactive close desk (`desk>`) shipped in TAPE-1, not
the full 25,000-row pipeline README.md walks through. Same agents, same code, deliberately
small `n` (≤400 synthetic rows) so a human can narrate the whole thing in about three minutes
and `scripts/record_demo.sh` can rerun it in well under two. Nothing below is fabricated —
every terminal block is what the desk actually prints; run `bash scripts/record_demo.sh` to
reproduce it yourself.

## Spoken script (~3 minutes)

**BEAT 1 — the problem (0:00–0:20).** Every unresolved reconciliation exception becomes
somebody's SLA clock the moment it's born. This is that same close desk a human already runs —
opened as one terminal, not a dashboard.

**BEAT 2 — pull + look (0:20–0:50).** `desk>` is the whole interface. `pull` brought in bank
data before this session even opened — forced to the synthetic `--overlay` source so pass 1 and
pass 2 are drawn from the same generator family (see "Honest disclosures" below); `analyze` runs
matching, guardrail, and routing and hands back real discrepancies — `discrepancies ready`.

**BEAT 3 — resolve, not approve (0:50–1:30).** No "approve" button. `resolve <exception_id> ...`
captures one human judgment call in the matcher's own structured fields and prints the
candidate rule in plain English, its support count against the current pile, and
`status=candidate` — nothing is applied yet.

**BEAT 4 — the only path that writes rules.json (1:30–1:55).** `promote <rule_id> yes-always`.
The literal word `yes-always` is the only thing that ever commits a rule.

**BEAT 5 — does it survive a new period? (1:55–2:35).** `next close` draws a genuinely new
pass 2 batch and shows the class delta, rules off vs. on. Then, for the camera, the same call
Agent 3 makes underneath, shown raw and verbatim: `resolved by rule: N`.

**BEAT 6 — status, logs, and the honest disclosures (2:35–3:00).** `status`/`logs` show where
the desk is and whether a Neatlogs span actually went out this turn. `quit` ends the session.

## Type this at desk>

Exactly what `scripts/record_demo.sh` feeds the desk — a one-shot `pull`, then three `--chat`
sessions, split apart only because each one needs a real value (an exception_id, then a
`rule_id`) the previous one just produced on disk, which can't be baked into a session's input
before it exists. Stdout below is a real, reproducible run of this script (seed=42, n=400 for
pass 1; next close's own pass 2 is fixed at n=300) — rerun `bash scripts/record_demo.sh` and you
will get byte-identical numbers (law L4):

```
$ python -m ledger_sense.operator pull --dir data/demo/pass1 --source synthetic --seed 42 --n-cases 400
source: synthetic (overlay)
bank.csv rows=455; ledger.csv rows=411
```

```
$ python -m ledger_sense.operator chat --dir data/demo/pass1 --pass2-dir data/demo/pass2
desk> analyze
bank lines=455
exceptions=88
top classes: amount_mismatch=31, duplicate=20, suspect_posting=19
guardrail: allow: 416/455 (91.43%); block: 37/455 (8.13%); hold: 2/455 (0.44%)
example exception_id: EXC-BANK-BK-P1-000004
discrepancies ready
desk> quit
```

```
desk> resolve EXC-BANK-BK-P1-000078 reference_transform --reference-transform exact --amount-class conflict \
      "AR ops treats an exact-reference match as the same payment even when the bank amount conflicts with the ledger -- a recurring FX/rounding pattern, not a one-off"
resolution_id=RES-f6863e1120ac5c18
exception_id=EXC-BANK-BK-P1-000078
rule_id=RULE-06643eb523a5
candidate predicate: amount_class=conflict AND reference=exact
support count against current exception pile: 26
status=candidate
desk> quit
```

`EXC-BANK-BK-P1-000078` (not `analyze`'s own arbitrary first-exception example above) is the one
this batch's `amount=conflict AND reference=exact` recurring pattern actually sits on —
`record_demo.sh` looks it up fresh every run rather than hardcoding it, so it self-heals if the
batch ever changes.

```
desk> promote RULE-06643eb523a5 yes-always
RULE-06643eb523a5 <- RES-f6863e1120ac5c18
desk> next close
generated pass 2 data in data/demo/pass2 (seed=42, n_cases=300, overlay)
class before -> after (rules off -> on):
  amount_mismatch: 22 -> 13 (dropped)
  duplicate: 15 -> 15
  suspect_posting: 12 -> 12
  timing: 10 -> 10
  unidentified_counterpart: 4 -> 4
rule_hits: 9
trace: data/demo/pass2/demo_trace.json
desk> status
desk> logs
neatlogs trace id: none (tracing disabled, or no span sent this turn)
desk> quit
```

Plus one raw, non-`desk>` call for BEAT 5's verbatim receipt — the exact call `next close`
already made internally, shown again standalone so `resolved by rule: N` prints in full instead
of folded into the desk's own `rule_hits: N` summary above:

```
$ python -m ledger_sense.learning apply-rules --outcomes data/demo/pass2/.desk/matching_out/match_outcomes.csv \
      --settlements data/demo/pass2/.desk/matching_out/ledger_settlements.csv \
      --ledger data/demo/pass2/ledger.csv --bank data/demo/pass2/bank.csv \
      --rules data/demo/pass1/rules.json --as-of 2026-06-04T01:56:56Z \
      --period-start 2026-01-01T20:53:22Z --period-end 2026-06-04T01:56:56Z --out-dir ...
rules loaded: 1
escalated lines seen: 26
escalated lines matching a rule's predicate: 21
vetoed by guardrail (would_block_or_hold != allow): 10
predicate hit but no ledger capacity remained: 2
resolved by rule: 9
  RULE-06643eb523a5: 9 lines resolved
```

**This is the moment the tape actually proves learning:** 9 pass-2 lines that would otherwise
have escalated into someone's SLA queue never get there — resolved before routing ever sees
them, from one human judgment call made once on pass 1.

## Run it yourself

```bash
bash scripts/record_demo.sh
```

- **PART A (mandatory) needs no API keys** — `DODO_API_KEY`/`OPENAI_API_KEY`/`NEATLOGS_API_KEY`
  are explicitly unset for it, so it never makes a live call and never spends money.
- Generates `data/demo/pass1` **only if it's missing** (`pull --source synthetic --seed 42
  --n-cases 400`, capped at ≤400 — never the 25,000-row pass1/pass2 the full pipeline uses);
  rerunning with data already on disk skips straight to `analyze` and finishes in a couple of
  seconds. `data/demo/pass2` is likewise only generated by `next close` if it isn't already
  there (fixed at seed=42, n=300 by the desk itself).
- **PART B (optional)** runs only if *this shell's own* environment already had a real key
  before PART A unset it — one honest, read-only line per configured integration, in a scratch
  directory, never touching `data/demo/*`. No key anywhere → PART B prints nothing at all.

## Honest disclosures, carried over from README.md

- **Dodo (source changed from the previous recording):** `pull` no longer relies on the desk's
  own live→cache→synthetic fallback here — `record_demo.sh` now passes `--source synthetic`
  explicitly, printed as `source: synthetic (overlay)`. Left to its own fallback, keyless `pull`
  lands on Agent 1's checked-in `dodo-cache` fixture (real, but only 20 rows from a different
  generator entirely), which shares no recurring defect class with pass 2's own synthetic draw —
  a rule learned from it can support-count fine on pass 1 but can never actually fire on pass 2.
  Forcing the same synthetic family for both passes is what makes `rule_hits>0` below honest
  rather than lucky. **Neatlogs:** with no key configured, `logs` prints `neatlogs trace id: none
  (tracing disabled, or no span sent this turn)` — no span was ever sent, and the desk says so
  plainly rather than staying silent about it.
- **Overlay:** `next close`'s own pass-2 generation always requests `--overlay`, but the
  generator only actually plants a labeled sibling cluster when this run's natural data doesn't
  already reach the demo threshold on its own — the desk's own summary line doesn't repeat that
  per-run detail, but the generator's own stdout always does, plainly, either way (run
  `python -m ledger_sense.data --seed 42 --pass-number 2 --n-cases 300 --overlay` directly to
  see it) — never silently presented as uniform, scripted data. The `rule_hits: 9` learning
  result above comes from a **naturally occurring** class (`amount_class=conflict AND
  reference=exact`), not from the overlay's own `fee_offset` siblings, which stay separate and
  unresolved in this batch since no promoted rule targets that class.
- **Ownership:** routing assigns a named owner from a fixed roster via a hash of the
  counterparty. It does not discover your real organizational owner — routing exists to feed
  learning and SLA tracking, not to replace org design.

This is a judgment-capture desk for one narrow slice of close work, run from a terminal — it
doesn't claim a headline accuracy number for the business; see README.md's own measured,
honestly split numbers for that.
