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


def build_scoreboard(*, pass1, pass2, rules, rule_hits, pass1_dir, pass2_dir, rules_path) -> dict:
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
    }
