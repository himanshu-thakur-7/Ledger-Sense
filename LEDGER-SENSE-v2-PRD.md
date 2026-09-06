# Ledger Sense v2 — real integrations

Source of truth for the v2 build (cards W8–W14 on BOARD.md). v1 is `Ledger-Sense-PRD.pdf` /
`main@d22f8d7` and is unchanged by this document — v2 only fills three seams v1 deliberately
left stubbed, disclosed in v1's own README/BOARD.md. If this document and v1's PDF ever
disagree on a v1 schema/threshold/filename, v1's PDF wins — v2 does not relitigate v1.

## Positioning (unchanged from v1, restated)

Ledger Sense is not "AI reconciliation." The matcher stays a deterministic, feature-weighted
scorer; the guardrail stays an independent, non-LLM policy layer; rule promotion stays
human-gated. v2 adds real external intelligence and real external data only inside the seams
v1 already designed for that purpose — it does not hand judgment authority to an LLM anywhere
it wasn't already an explicit, bounded assist.

## What changes

| Area | v1 (shipped) | v2 (this doc) |
|---|---|---|
| Matching adjudication (gray-zone) | `StubAdjudicator`, 0 API calls | Real `OpenAIAdjudicator` behind the same `Adjudicator` Protocol |
| Resolution-learning rationale | Fully manual predicate entry | OpenAI-suggested predicate from free-text rationale; `--confirm yes-always` unchanged |
| Routing classification | Deterministic first-hit, rule 7 = `unidentified_counterpart` fallback | OpenAI fallback classifier for rule-7 cases only, still re-checked by the unchanged guardrail |
| Bank-side data | 100% synthetic | Config-switchable: synthetic (default) or real Dodo Payments sandbox |
| Observability | `print()` + CSV/JSON only | Neatlogs spans around every agent entrypoint, incl. LLM cost/latency/tokens |
| Config/secrets | None | `.env`-based `config.py`; no secrets hardcoded or logged |
| Architecture shape | CLI/batch over flat files | **Unchanged** — no DB, no API service, no UI, no infra |

## Explicitly out of scope (mirrors v1's own §14 discipline)

No database/persistent-store migration; no API/service wrapper — stays CLI/batch/files; no
UI/dashboard; no production deployment/infra (Docker/k8s/hosting); no multi-tenant support; no
live (non-sandbox) Dodo payment processing; no OpenAI fine-tuning; no widening of the matcher's
or guardrail's decision authority beyond the bounded seams below.

## Locked decisions (human-approved, round 1 — do not relitigate)

1. **Dodo/ledger-pairing (W11):** pull-then-synthesize. Pull existing Dodo sandbox
   transactions first, then synthesize matching `ledger.csv` rows around them, mirroring the
   generator's defect-injection logic. Fully automated, repeatable — no manual dashboard
   clicking required before each demo run.
2. **OpenAI model + cost cap (W9/W12/W13):** cheap tier (`gpt-4o-mini`-class model),
   **$1.00 per full pipeline run** cap, both overridable via env vars
   (`LEDGER_SENSE_OPENAI_MODEL`, `LEDGER_SENSE_LLM_COST_CAP_USD`).
3. **Shared LLM client (W9/W12/W13):** one thin wrapper module (`llm_client.py`, built in W8)
   — single point of retry/timeout/cost-cap/redaction enforcement. W9 is first to use it; W12
   and W13 import the same module, never reimplement.
4. **API keys:** the human sets `OPENAI_API_KEY` / `DODO_API_KEY` / `DODO_ENVIRONMENT=sandbox`
   / `NEATLOGS_API_KEY` directly via `ao project set-config ledger-sense --env "KEY=..."`.
   Never pasted through chat, never committed, never logged.

## Standing v2 laws (extend v1's L1–L17; cite the relevant ones in every v2 spawn)

- **L18** Absence of any live-mode API key must degrade gracefully to v1 stub/synthetic/
  no-tracing behavior — never crash, never change v1's zero-key output.
- **L19** No secrets hardcoded or logged anywhere. Neatlogs spans and any debug output must
  redact credential-shaped values before emission.
- **L20** Every new external-API code path is unit-tested against a mocked client. The full
  pytest suite stays 100% offline/deterministic — zero live network calls, zero API spend, in
  CI or in any worker's own test run.
- **L21** LLM output at every seam (adjudication, rationale-assist, routing fallback) is bounded
  to its existing seam only — never given authority beyond what v1's deterministic layer
  already couldn't resolve. The guardrail always independently re-derives and is never
  bypassed or trusted-through.
- **L22** Per-run cost/call caps, timeouts, and bounded retries are mandatory on every external
  API client, enforced through the shared `llm_client.py` (locked decision 3).

## Integration specs

### OpenAI — matching adjudication (W9)
New `src/ledger_sense/matching/llm_adjudicator.py` implementing v1's existing `Adjudicator`
Protocol (`matching/adjudication.py`) — `NoneAdjudicator`/`StubAdjudicator` remain available
and are the default when no key is configured. Applies only to the existing gray-zone
candidates the deterministic scorer already can't resolve confidently — never full-scan
free-form judgment. Structured JSON response: `{decision: match|no_match|needs_human,
confidence, rationale}`. `temperature=0`, timeout, bounded retries, the locked $1/run cap,
response cache keyed by `(ledger_id, bank_txn_id)`. On any API failure or cap breach: fall back
to `StubAdjudicator` behavior for that batch — never blocks or crashes. `llm_calls`/
`llm_is_stub`/token usage already flow into `match_outcomes.csv` and the scoreboard unmodified
(verified against v1's actual code before this doc was finalized).

### OpenAI — resolution-learning rationale assist (W12)
New `src/ledger_sense/learning/llm_rationale.py`. Takes a human's structured `Resolution`
(enum `resolution_type` + free-text rationale) and suggests a candidate feature-space evidence
predicate in `learning/predicate.py`'s existing vocabulary — never a new one. The suggestion is
clearly labeled as a suggestion; `ledger_sense promote RULE-... --confirm yes-always` remains
mandatory and unchanged; the human can edit or reject the suggestion. `manual_one_off`/
`no_pattern` never receive a suggestion (unchanged v1 rule).

### OpenAI — routing fallback classification (W13)
Extends the consumption of `routing/classify.py`'s existing rule 7 only (`unidentified_
counterpart`, "no earlier condition matched") — rules 1–6 are never touched or intercepted.
Classifies into the same fixed 5-category taxonomy with a confidence score. Every
LLM-classified case is tagged/auditable. The independent guardrail re-check is completely
unaffected — it never trusts the classification source, only the row's own facts.

### Dodo Payments — real bank-side source (W11)
New `src/ledger_sense/data/dodo_source.py` (+ `dodo_pairing.py` for the locked
pull-then-synthesize strategy). Output in the exact `BankTransaction` shape v1's synthetic
generator already produces (verified against `data/models.py`) — matching/routing/guardrail/
learning/metrics need zero changes to consume it. Config switch
`LEDGER_SENSE_DATA_SOURCE=synthetic|dodo` (default `synthetic` — v1's deterministic/CI
behavior is untouched unless explicitly opted in). Sandbox credentials via `DODO_API_KEY` /
`DODO_ENVIRONMENT=sandbox`. Idempotent ingestion (dedup by Dodo transaction ID), pagination,
field mapping into the existing schema (amounts as Decimal/cents, never float — law L3).

### Neatlogs — tracing/observability (W10)
Wrap each of the 5 agent CLI entrypoints (data, matching, routing, guardrail, learning,
metrics) in a named Neatlogs span via a small `tracing.py` helper. Each span captures: agent
name, duration, input/output row counts, guardrail allow/block/hold breakdown, and LLM call
count/tokens/estimated cost when present. Config via `NEATLOGS_API_KEY`; spans must never log
secrets — redact before emitting. Tracing failures degrade silently, never crash the pipeline.

## Success metrics (additive to v1's STR/exception-count metrics)

- Match-rate lift on gray-zone candidates attributable specifically to the real adjudicator.
- Cost per resolved exception (OpenAI $ spent ÷ STR points gained) — a CFO-relevant number,
  surfaced in the scoreboard.
- Latency delta per batch run: synthetic+stub mode vs. live mode.
- 100% of agent runs produce a Neatlogs trace when tracing is enabled.
- A live-mode end-to-end run (real OpenAI + real Dodo sandbox data) completes and produces a
  valid scoreboard, with a clear fallback demonstrated when a key is intentionally omitted.

## Build sequence

```
W8 config/secrets foundation (no dependencies — everything below needs this)
  ├─ W9  OpenAI matching adjudicator         ⎤
  ├─ W10 Neatlogs tracing                    ⎥ parallel, all depend only on W8
  ├─ W11 Dodo Payments sandbox source        ⎦
  ├─ W12 OpenAI rationale assist               (ideally after W9 — shares llm_client.py)
  └─ W13 OpenAI routing fallback               (ideally after W9 — shares llm_client.py)
       └─ W14 metrics v2 + docs + live smoke test (needs W9, W10, W11, W12, W13 all merged)
```
