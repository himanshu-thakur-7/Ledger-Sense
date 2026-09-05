# W2 — Agent 1

```sh
python -m ledger_sense.matching \
  --ledger data/pass1/ledger.csv --bank data/pass1/bank.csv --out-dir data/pass1
pytest -q tests/test_matching.py tests/test_matching_isolation.py
```

`--adjudicator none` disables local adjudication. The default `stub` is an explicit
deterministic heuristic, **not an LLM**. Both bundled adapters report
`llm_is_stub=True`; provider calls are measured by an adapter counter delta and are
zero. A future provider implements the `Adjudicator` protocol with one batched
call containing each bank record, its top three candidates and feature breakdown,
and the cheap-tier rejection reason. It must increment its provider-call counter.
Unknown/repeated bank IDs, candidates outside the offered three, and invalid
confidence values fail closed. Both original-best and replacement-candidate
interlocks are rechecked after adjudication, then settlement rechecks capacity.

## Implementation contract

- Index once by normalized reference, squashed name key4, and absolute cent bucket
  (`abs(cents) // 100`). Queries union reference and key4-intersected ±4 buckets.
  Only an empty union permits key4-intersected same-sign 15–85% partial candidates.
  Keep at most 40, closest absolute amounts first, ledger ID breaking ties. No
  full-ledger fallback exists.
- Scoring uses the five §5.3 weights without rounded intermediates. Missing bank
  reference drops the reference feature and divides the remaining weighted sum by
  60, scaling to 100. A reference naming another known ledger is wrong evidence
  (0), not missing evidence. Only unknown/corrupted references can earn fuzzy 0.6.
  The explicit weights yield a maximum of 60 with reference=0, notwithstanding
  the approximate score in the spec's explanatory prose.
- Fuzzy matching uses dependency-free classic `SequenceMatcher` token-set and
  matching-block-aligned partial ratios. Ratios are calculated from integer match
  counts with Decimal division, not float-returning stdlib methods. Names are
  uppercased, punctuation becomes spaces, and whitespace is collapsed. Reference
  and key4 normalization keep alphanumeric characters only.
- Exact reference + exact signed cents + normalized currency short-circuits to
  100. Name evidence is JSON `null` with `short_circuit=true`, **not invented**;
  date evidence is still available. Otherwise name similarity is floored at 0.40.
- Acceptance is ≥88 with margin ≥6 or one candidate; the exact-reference partial
  exception is ≥78 with name ≥0.70. Unaccepted scores ≥45 escalate (including
  ambiguous scores ≥88); scores <45 reject. Interlock violations escalate with
  `anomalous_amount` or `currency_conflict` and cannot settle through either tier.
- Greedy order is score descending, bank ID ascending. Each tier uses the same
  capacity object, in integer cents. Accepted partials consume their posted cents.
  An FX overage is capped at available book cents; an FX underpayment preserves
  the residual. Ordinary partials are never clipped to fit insufficient capacity.
- Duplicate retries that cannot fit are recognized by amount, currency and
  normalized reference (name key4 substitutes when the reference is absent),
  scoped to the selected ledger. Noisy names, dates, narratives and bank accounts
  need not be identical. Two equal half-payments can both settle if capacity fits.
  A retry never contributes to `n_parts` or matched cents.
- The stub can accept only exact/fx amount with name ≥0.90, date ≥0.50,
  non-reference evidence ≥88 and margin ≥6 (or one candidate). Its evidence is
  the remaining weighted sum rescaled to 100; it never scans beyond the top three.

## CSV contract

The two headers follow §5.8 exactly. Output rows are sorted by bank/ledger ID.
Money is signed and always formatted to two decimal places; no float money is
used. CSV newlines and JSON serialization are deterministic.

`status` is `matched`, `escalated` or `rejected`. `tier` is `cheap` or `llm` (the
stub uses the latter, with the explicit stub flag). `relation` is `exact`,
`partial`, `duplicate` or empty. An FX match uses relation `exact`, with its actual
amount class retained in the feature evidence. Duplicate rows have status
`escalated`, reason `duplicate_of_matched`, and `matched_amount=0.00`.

`candidates` is a JSON array of up to 40 score-ranked objects containing
`ledger_id`, `score`, `features`, and the original `ledger` row. `features` is a
JSON object with the selected candidate's five feature values, amount class,
`short_circuit`, plus the original `bank` row, normalized `counterparty_key`,
`currency_normalized`, and signed `amount_delta_cents`. This carries input
context through the file spine without downstream imports of Agent 1 internals.
Decimal feature values are JSON strings to preserve exactness; `null` means
not evaluated/missing. `score` is the selected candidate's original cheap score;
`margin` remains the original cheap top-two gap (one candidate: its score).
Stub/provider confidence is separate, never relabeled as cheap evidence.

Ledger settlement reasons are `fully_settled`, `partially_settled`, and
`never_settled`. `bank_txn_ids` is a JSON array in settlement order. Zero-value
books without any assigned line are `never_settled`, not silently closed.

## Known limitations and measured run

The §5.4 near-relative-name decoy remains an intentional regression: an exact
reference to “Alpha Systems Group” can beat the real “Alpha Systems Trading Co”
candidate through the partial exception. Fixing this requires a scoring-design
change, not a broader search. Tests pin this failure mode rather than claiming
the matcher is universally precise. Indistinguishable repeated equal partials
also cannot be identified as duplicates while enough capacity remains.

Measured locally with the **unchanged** generator:

```sh
python -m ledger_sense.data --seed 42 --pass-number 1 --n-cases 25000 --out-dir data/pass1
```

| Measurement | Result |
| --- | ---: |
| Ledger entries / bank lines | 24,500 / 27,250 |
| Cheap matches / bank lines | 22,872 / 27,250 = **83.9339%** |
| Matches including stub | 24,336 / 27,250 = **89.3064%** |
| Matched precision (test-only truth comparison) | **1.000000** |
| Duplicate cases with one settlement and one duplicate | **1,250 / 1,250** |
| Sign/zero bait auto-matched / orphan banks auto-matched | **0 / 0** |
| Provider calls | **0** |
| Both output files byte-identical on two full runs | **Yes** |

The measured rates are below the PDF's approximate 88–89% cheap and 92–93%
overall targets; the generator's bank cardinality is also different from the
PDF's calibrated reference. Neither data nor thresholds were rewritten to hit
those targets. The command above has overlay disabled. No results here claim a
learning effect or a pass-2 improvement.

The full-batch regression runs when `data/pass1` exists; otherwise it skips, while
mini-fixture and boundary tests always run. Set `MATCHING_BATCH_DIR` to verify
another generated batch. Large generated/output CSV files are not committed.
