"""Pure pass-1-vs-pass-2 comparison (spec §9 / BOARD.md W6). No file I/O in
this module -- ``cli.py`` owns every read (mirrors the same
computation/I/O split ``ledger_sense.learning.apply``/``cli`` already keep,
not an import of it -- law L1). Every function here takes already-parsed
rows and returns plain ``dict``/``str``/``int``/``bool`` values only, so the
result is directly ``json.dumps``-able and safe for ``tests/test_metrics.py``
to drive with in-memory fixtures.

All percentages are computed in ``Decimal`` and returned as fixed 2-decimal
strings (law L3: never float money or float-derived math).
"""

from decimal import Decimal

from .classify import class_histogram, class_key_str


class ScoreboardError(ValueError):
    """The inputs handed to the scoreboard don't add up -- refused rather
    than printed (spec: never fabricate a number)."""


def _pct(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.00"
    return str((Decimal(numerator) / Decimal(denominator) * Decimal(100)).quantize(Decimal("0.01")))


def straight_through(outcomes, settlements_by_id) -> dict:
    """Naive STR: matched AND its ledger side is fully settled -- the exact
    formula ``tests/test_routing.py`` and ``tests/test_learning.py`` already
    use (re-derived here from the same public output columns, not imported,
    so a drift in one place fails a test instead of silently disagreeing)."""
    total = len(outcomes)
    straight = sum(
        1 for row in outcomes
        if row["status"] == "matched" and settlements_by_id.get(row["ledger_id"], {}).get("reason") == "fully_settled"
    )
    return {"straight": straight, "total": total, "pct": _pct(straight, total)}


def ground_truth_map(match_link_rows) -> dict:
    """``bank_txn_id -> ledger_id`` straight off ``match_links.csv`` -- the
    one file law L2 reserves for this agent alone. A bank line absent from
    this map (an ``orphan_bank`` case) has no true counterpart at all: any
    outcome that claims to have matched it is a false positive by
    definition."""
    return {row["bank_txn_id"]: row["ledger_id"] for row in match_link_rows}


def real_straight_through(outcomes, settlements_by_id, truth: dict) -> dict:
    """Ground-truth-checked STR and match precision (spec §9.1: "never
    asserted"). A claimed match only counts as correct if it names the SAME
    ledger_id ``match_links.csv`` says belongs to that bank line -- a
    confident-but-wrong match earns nothing here, unlike the naive STR
    above, which only knows Agent 1 claimed success."""
    total = len(outcomes)
    claimed = correct = straight_correct = 0
    for row in outcomes:
        if row["status"] != "matched":
            continue
        claimed += 1
        truth_ledger = truth.get(row["bank_txn_id"])
        if truth_ledger is not None and truth_ledger == row["ledger_id"]:
            correct += 1
            if settlements_by_id.get(row["ledger_id"], {}).get("reason") == "fully_settled":
                straight_correct += 1
    return {
        "claimed_matches": claimed,
        "correct_matches": correct,
        "straight_through_correct": straight_correct,
        "total": total,
        "precision_pct": _pct(correct, claimed),
        "real_str_pct": _pct(straight_correct, total),
    }


def guardrail_split(release_rows) -> dict:
    """allow/block/hold rates from ``release_decisions.csv`` -- spec §9.1's
    own sanity check: these should hold roughly steady across passes,
    since a learned rule is vetoed outright if Agent 4 would block or hold
    that line (law L12)."""
    total = len(release_rows)
    counts = {"allow": 0, "block": 0, "hold": 0}
    for row in release_rows:
        verdict = row["verdict"]
        counts[verdict] = counts.get(verdict, 0) + 1
    result = {"total": total}
    for verdict, count in counts.items():
        result[f"{verdict}_count"] = count
        result[f"{verdict}_pct"] = _pct(count, total)
    return result


def class_diff(pass1_exceptions, pass1_features, pass2_exceptions, pass2_features) -> dict:
    """Pass-1 vs pass-2 exception-class histogram diff (spec §9.1: "by
    class, not a raw count"). A class is "eliminated" when it had at least
    one pass-1 exception and has exactly zero in pass 2."""
    hist1 = class_histogram(pass1_exceptions, pass1_features)
    hist2 = class_histogram(pass2_exceptions, pass2_features)
    keys = sorted(set(hist1) | set(hist2), key=class_key_str)
    rows = []
    for key in keys:
        c1, c2 = hist1.get(key, 0), hist2.get(key, 0)
        rows.append({
            "class": class_key_str(key),
            "pass1_count": c1,
            "pass2_count": c2,
            "delta": c2 - c1,
            "eliminated": c1 > 0 and c2 == 0,
        })
    eliminated = [row["class"] for row in rows if row["eliminated"]]
    return {"rows": rows, "eliminated_classes": eliminated, "eliminated_count": len(eliminated)}


def rule_trace(rule_hit_rows, rules_by_id) -> list:
    """auto-resolved row -> rule_id -> resolution_id, read straight off
    ``rule_hits.csv`` (never recomputed); ``rules.json`` only enriches each
    row with the human-facing fields (plain English, who promoted it) that
    ``rule_hits.csv`` has no room for."""
    trace = []
    for hit in rule_hit_rows:
        rule = rules_by_id.get(hit["rule_id"], {})
        trace.append({
            "bank_txn_id": hit["bank_txn_id"],
            "ledger_id": hit["ledger_id"],
            "rule_id": hit["rule_id"],
            "resolution_id": hit["resolution_id"],
            "resolution_type": hit["resolution_type"],
            "applied_cents": hit["applied_cents"],
            "guardrail_verdict": hit["guardrail_verdict"],
            "plain_english": rule.get("plain_english", ""),
            "promoted_by": rule.get("promoted_by", ""),
            "promoted_at": rule.get("promoted_at", ""),
        })
    return trace


def llm_cost_summary(*, llm_cost_usd) -> dict:
    """Total OpenAI $ actually spent by a real (non-stub) run, as measured by
    the caller -- never estimated here (spec: LEDGER-SENSE-v2-PRD.md W14
    success metrics, "Cost per resolved exception").

    ``scoreboard.py`` only ever reads files already on disk (module docstring
    above); nothing in ``match_outcomes.csv`` carries a per-call dollar
    figure (only ``llm_model``/``llm_confidence``/``llm_is_stub``, W9's
    existing columns), so the actual spend for a live run is supplied by the
    caller -- typically read off the real ``OpenAIAdjudicator``'s underlying
    ``LLMClient.cumulative_cost_usd`` right after the matching CLI returns.
    ``llm_cost_usd`` is ``None`` for every v1/offline/CI run (law L20/L18) --
    that is reported as ``measured: False`` rather than fabricated as a $0.00
    spend, which would misrepresent "never called" as "called for free".
    """
    if llm_cost_usd is None:
        return {"measured": False}
    return {"measured": True, "total_cost_usd": str(Decimal(llm_cost_usd))}


def adjudicator_lift(*, stub_str_real: dict, llm_str_real: dict, llm_cost_usd) -> dict:
    """Match-rate lift and cost-per-STR-point attributable specifically to
    the real adjudicator (PRD success metric), from two ``real_straight_through``
    summaries computed over the *same* underlying batch (identical
    ``ledger.csv``/``bank.csv``) -- one produced with ``--adjudicator stub``,
    one with ``--adjudicator auto`` against a configured real key. Passing
    two summaries from different batches would silently misattribute
    ordinary two-draw variance to the adjudicator -- that discipline is the
    caller's (``cli.py``'s ``--adjudicator-stub-dir``/``--adjudicator-llm-dir``
    docstrings), not something this pure function can verify.

    ``llm_cost_usd`` is the real run's measured OpenAI spend (see
    ``llm_cost_summary``) -- ``None`` when not measured (v1/CI), in which
    case cost-per-point is reported ``None`` rather than fabricated as
    ``0``/infinite. Likewise when the real adjudicator resolved zero
    *additional* straight-through-and-correct rows this run (``points_gained
    <= 0``) -- dividing a real dollar spend by a non-positive gain would
    misrepresent "no measured lift" as a cost figure, so it is reported
    ``None`` (a real, disclosable outcome) instead.
    """
    stub_correct = stub_str_real["straight_through_correct"]
    llm_correct = llm_str_real["straight_through_correct"]
    points_gained = llm_correct - stub_correct
    cost_per_point = None
    if llm_cost_usd is not None and points_gained > 0:
        cost_per_point = str((Decimal(llm_cost_usd) / Decimal(points_gained)).quantize(Decimal("0.0001")))
    return {
        "stub_straight_through_correct": stub_correct,
        "llm_straight_through_correct": llm_correct,
        "str_points_gained": points_gained,
        "stub_real_str_pct": stub_str_real["real_str_pct"],
        "llm_real_str_pct": llm_str_real["real_str_pct"],
        "cost_per_str_point_usd": cost_per_point,
    }


def latency_delta(*, stub_duration_seconds, live_duration_seconds) -> dict:
    """Wall-clock delta, synthetic+stub mode vs. full live mode, over the
    same batch (PRD success metric) -- both durations are the caller's own
    measurement (e.g. wrapping each pipeline run with ``time.monotonic()``)
    since ``scoreboard.py`` never runs Agents 1-4 itself (module docstring)
    and neither figure is written to any file this package reads. Decimal
    seconds in, Decimal seconds out (law L3) -- never a float literal.
    Missing either side (the default, no-live-run case) -- reported
    ``measured: False`` rather than a fabricated ``0``-second delta.
    """
    if stub_duration_seconds is None or live_duration_seconds is None:
        return {"measured": False}
    stub_s = Decimal(stub_duration_seconds)
    live_s = Decimal(live_duration_seconds)
    return {
        "measured": True,
        "stub_duration_seconds": str(stub_s),
        "live_duration_seconds": str(live_s),
        "delta_seconds": str(live_s - stub_s),
    }


def trace_coverage(*, entrypoints_run, spans_emitted) -> dict:
    """Neatlogs trace-coverage: spans actually emitted / entrypoints run
    (PRD success metric: "100% of agent runs produce a Neatlogs trace when
    tracing is enabled"). ``tracing.py`` sends spans directly to the real
    Neatlogs service and writes nothing to any file this package reads, so
    both counts are the caller's own tally (e.g. counting successful CLI
    entrypoint invocations against a Neatlogs-side span count for the same
    run) rather than something ``scoreboard.py`` could recompute from disk.
    Missing either count (the default, tracing-disabled case) -- reported
    ``measured: False`` rather than asserting a 0% or 100% coverage nobody
    actually counted.
    """
    if entrypoints_run is None or spans_emitted is None:
        return {"measured": False}
    return {
        "measured": True,
        "entrypoints_run": entrypoints_run,
        "spans_emitted": spans_emitted,
        "coverage_pct": _pct(spans_emitted, entrypoints_run),
    }


def _pass_summary(outcomes, settlements, exceptions, release_decisions, match_links, queues, guardrail_audit) -> dict:
    settlements_by_id = {row["ledger_id"]: row for row in settlements}
    truth = ground_truth_map(match_links)
    return {
        "str_naive": straight_through(outcomes, settlements_by_id),
        "str_real": real_straight_through(outcomes, settlements_by_id, truth),
        "exceptions_remaining": len(exceptions),
        "guardrail_split": guardrail_split(release_decisions),
        # Read straight off owner_queues.csv/guardrail_audit.csv (both required
        # inputs) so a broken or empty upstream file fails loudly here too,
        # even though neither feeds a computed ratio above.
        "owner_queue_count": len(queues),
        "guardrail_audit_rows": len(guardrail_audit),
    }


def build_scoreboard(*, pass1, pass2, rules, rule_hits, pass1_dir, pass2_dir, rules_path,
                      llm_cost_usd=None, adjudicator_stub=None, adjudicator_llm=None,
                      stub_duration_seconds=None, live_duration_seconds=None,
                      entrypoints_run=None, spans_emitted=None) -> dict:
    """Assemble the full scoreboard dict from already-parsed pass-1/pass-2
    file contents.

    ``pass1``/``pass2`` are dicts with keys ``outcomes``, ``settlements``,
    ``exceptions``, ``features`` (``bank_txn_id -> parsed features``),
    ``release_decisions``, ``match_links``, ``queues``, ``guardrail_audit`` --
    exactly what ``cli.py`` reads off disk via ``ledger_sense.metrics.io``.

    Refuses (``ScoreboardError``) rather than prints a number when the
    inputs are internally inconsistent: a ``rule_hits.csv`` row naming a
    ``rule_id`` absent from ``rules.json``, or a pass-2 auto-resolve that
    ``rule_hits.csv`` doesn't account for (acceptance #3: the trace table
    must cover 100% of rule-driven auto-resolves).

    Every remaining keyword (v2, LEDGER-SENSE-v2-PRD.md W14) is optional and
    additive -- omitted entirely, every one defaults to ``None`` and the
    scoreboard's ``v2`` section reports each sub-metric ``measured: False``
    rather than a fabricated number, so a v1/offline/CI caller (the existing
    ``build_scoreboard(pass1=..., pass2=..., ...)`` call shape) is completely
    unaffected:
      * ``llm_cost_usd`` -- real OpenAI $ spent this run (see ``llm_cost_summary``).
      * ``adjudicator_stub``/``adjudicator_llm`` -- pass-shaped dicts (same
        ``outcomes``/``settlements``/``match_links`` keys as ``pass1``/``pass2``)
        from the *same* underlying batch matched twice, once per adjudicator
        (see ``adjudicator_lift``).
      * ``stub_duration_seconds``/``live_duration_seconds`` -- wall-clock
        seconds for the two runs being compared (see ``latency_delta``).
      * ``entrypoints_run``/``spans_emitted`` -- Neatlogs trace-coverage
        counts (see ``trace_coverage``).
    """
    rules_by_id = {rule["rule_id"]: rule for rule in rules}
    missing_rule_ids = {hit["rule_id"] for hit in rule_hits} - set(rules_by_id)
    if missing_rule_ids:
        raise ScoreboardError(
            f"rule_hits.csv references rule_id(s) not present in rules.json: {sorted(missing_rule_ids)}"
        )

    auto_resolved_by_rule = sum(1 for row in pass2["outcomes"] if row.get("reason") == "resolved_by_rule")
    if auto_resolved_by_rule != len(rule_hits):
        raise ScoreboardError(
            f"trace-table coverage mismatch: pass-2 match_outcomes.csv has {auto_resolved_by_rule} row(s) with "
            f"reason=resolved_by_rule but rule_hits.csv has {len(rule_hits)} row(s) -- refusing to print an "
            "unverified trace table"
        )

    pass1_summary = _pass_summary(
        pass1["outcomes"], pass1["settlements"], pass1["exceptions"], pass1["release_decisions"],
        pass1["match_links"], pass1["queues"], pass1["guardrail_audit"],
    )
    pass2_summary = _pass_summary(
        pass2["outcomes"], pass2["settlements"], pass2["exceptions"], pass2["release_decisions"],
        pass2["match_links"], pass2["queues"], pass2["guardrail_audit"],
    )
    pass2_summary["rule_driven_auto_resolves"] = auto_resolved_by_rule
    pass2_summary["trace_coverage_pct"] = _pct(len(rule_hits), auto_resolved_by_rule) if auto_resolved_by_rule else "100.00"

    adjudicator_lift_result = {"measured": False}
    if adjudicator_stub is not None and adjudicator_llm is not None:
        stub_real = real_straight_through(
            adjudicator_stub["outcomes"],
            {row["ledger_id"]: row for row in adjudicator_stub["settlements"]},
            ground_truth_map(adjudicator_stub["match_links"]),
        )
        llm_real = real_straight_through(
            adjudicator_llm["outcomes"],
            {row["ledger_id"]: row for row in adjudicator_llm["settlements"]},
            ground_truth_map(adjudicator_llm["match_links"]),
        )
        adjudicator_lift_result = {
            "measured": True,
            **adjudicator_lift(stub_str_real=stub_real, llm_str_real=llm_real, llm_cost_usd=llm_cost_usd),
        }

    return {
        "inputs": {
            "pass1_dir": str(pass1_dir),
            "pass2_dir": str(pass2_dir),
            "rules_path": str(rules_path),
        },
        "pass1": pass1_summary,
        "pass2": pass2_summary,
        "learned_rule_count": len(rules),
        "exception_classes": class_diff(
            pass1["exceptions"], pass1["features"], pass2["exceptions"], pass2["features"]
        ),
        "rule_trace": rule_trace(rule_hits, rules_by_id),
        "v2": {
            "llm_cost": llm_cost_summary(llm_cost_usd=llm_cost_usd),
            "adjudicator_lift": adjudicator_lift_result,
            "latency_delta": latency_delta(
                stub_duration_seconds=stub_duration_seconds, live_duration_seconds=live_duration_seconds
            ),
            "trace_coverage": trace_coverage(entrypoints_run=entrypoints_run, spans_emitted=spans_emitted),
        },
    }
