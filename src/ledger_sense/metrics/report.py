"""Terminal rendering of a scoreboard dict (spec §9.1 / BOARD.md W6, locked
Q1: "terminal output, no rendering step required").

Pure string building from the exact dict ``scoreboard.build_scoreboard``
returns -- no computation happens here, so the printed numbers and
``scoreboard.json`` can never drift apart (they're read off the same dict).

Two tables (exception classes, rule trace) are capped for terminal display
when very large -- ``scoreboard.json`` always carries the complete,
untruncated list; the cap is purely to keep a real 25k-case run's terminal
output readable, never to hide a number that would otherwise be printed.
"""

_CLASS_TABLE_DISPLAY_LIMIT = 40
_TRACE_TABLE_DISPLAY_LIMIT = 200


def _line(*cells, widths) -> str:
    return "  ".join(str(cell).ljust(width) for cell, width in zip(cells, widths))


def _pass_block(label: str, summary: dict) -> list:
    lines = [f"-- {label} --"]
    str_naive, str_real = summary["str_naive"], summary["str_real"]
    lines.append(
        f"  STR (naive, matched+settled): {str_naive['straight']}/{str_naive['total']} ({str_naive['pct']}%)"
    )
    lines.append(
        f"  STR (real, vs match_links.csv): {str_real['straight_through_correct']}/{str_real['total']} "
        f"({str_real['real_str_pct']}%)"
    )
    lines.append(
        f"  Match precision (vs match_links.csv): {str_real['correct_matches']}/{str_real['claimed_matches']} "
        f"({str_real['precision_pct']}%)"
    )
    lines.append(f"  Exceptions remaining: {summary['exceptions_remaining']}")
    gr = summary["guardrail_split"]
    lines.append(
        f"  Guardrail split: allow {gr['allow_count']}/{gr['total']} ({gr['allow_pct']}%), "
        f"block {gr['block_count']}/{gr['total']} ({gr['block_pct']}%), "
        f"hold {gr['hold_count']}/{gr['total']} ({gr['hold_pct']}%)"
    )
    if "rule_driven_auto_resolves" in summary:
        lines.append(
            f"  Rule-driven auto-resolves: {summary['rule_driven_auto_resolves']} "
            f"(trace coverage {summary['trace_coverage_pct']}%)"
        )
    return lines


def _v2_block(v2: dict) -> list:
    """v2 (LEDGER-SENSE-v2-PRD.md W14) additive lines -- only ever printed for
    the sub-metrics a caller actually measured (``measured: True``); an
    unmeasured sub-metric (the default, v1/offline case) prints nothing at
    all, so a plain v1 invocation's terminal output is completely unchanged
    from before this section existed."""
    lines = ["-- v2 (live-mode) --"]
    printed = False

    cost = v2.get("llm_cost", {})
    if cost.get("measured"):
        lines.append(f"  OpenAI cost this run: ${cost['total_cost_usd']}")
        printed = True

    lift = v2.get("adjudicator_lift", {})
    if lift.get("measured"):
        cost_per_point = lift["cost_per_str_point_usd"]
        cost_per_point_display = f"${cost_per_point}" if cost_per_point is not None else "n/a (no STR gain measured)"
        lines.append(
            f"  Real adjudicator STR lift: {lift['stub_straight_through_correct']} -> "
            f"{lift['llm_straight_through_correct']} ({lift['str_points_gained']:+d} points); "
            f"cost/point: {cost_per_point_display}"
        )
        printed = True

    latency = v2.get("latency_delta", {})
    if latency.get("measured"):
        lines.append(
            f"  Latency delta (stub+synthetic vs. live): {latency['stub_duration_seconds']}s -> "
            f"{latency['live_duration_seconds']}s (delta {latency['delta_seconds']}s)"
        )
        printed = True

    trace = v2.get("trace_coverage", {})
    if trace.get("measured"):
        lines.append(
            f"  Neatlogs trace coverage: {trace['spans_emitted']}/{trace['entrypoints_run']} "
            f"({trace['coverage_pct']}%)"
        )
        printed = True

    if not printed:
        return []
    lines.append("")
    return lines


def render_report(scoreboard: dict) -> str:
    lines = []
    inputs = scoreboard["inputs"]
    lines.append("Ledger Sense -- Agent 5 scoreboard (spec §9)")
    lines.append(f"pass1_dir={inputs['pass1_dir']}  pass2_dir={inputs['pass2_dir']}  rules={inputs['rules_path']}")
    lines.append("")
    lines.extend(_pass_block("Pass 1", scoreboard["pass1"]))
    lines.append("")
    lines.extend(_pass_block("Pass 2", scoreboard["pass2"]))
    lines.append("")
    lines.extend(_v2_block(scoreboard.get("v2", {})))
    lines.append(f"Learned rule count: {scoreboard['learned_rule_count']}")
    lines.append("")

    classes = scoreboard["exception_classes"]
    lines.append("-- Exception classes (counterparty | amount-bucket | reference-pattern) --")
    lines.append(f"  {classes['eliminated_count']} class(es) eliminated (pass1 > 0, pass2 == 0):")
    for cls in classes["eliminated_classes"]:
        lines.append(f"    - {cls}")
    widths = (60, 10, 10, 8)
    lines.append(_line("class", "pass1", "pass2", "delta", widths=widths))
    rows = classes["rows"]
    for row in rows[:_CLASS_TABLE_DISPLAY_LIMIT]:
        lines.append(_line(row["class"], row["pass1_count"], row["pass2_count"], row["delta"], widths=widths))
    if len(rows) > _CLASS_TABLE_DISPLAY_LIMIT:
        lines.append(f"  ... {len(rows) - _CLASS_TABLE_DISPLAY_LIMIT} more class(es) in scoreboard.json")
    lines.append("")

    trace = scoreboard["rule_trace"]
    lines.append(f"-- Rule trace (auto-resolved row -> rule_id -> resolution_id), {len(trace)} row(s) --")
    trace_widths = (24, 24, 18, 22, 20)
    lines.append(_line("bank_txn_id", "ledger_id", "rule_id", "resolution_id", "resolution_type", widths=trace_widths))
    for hit in trace[:_TRACE_TABLE_DISPLAY_LIMIT]:
        lines.append(_line(
            hit["bank_txn_id"], hit["ledger_id"], hit["rule_id"], hit["resolution_id"], hit["resolution_type"],
            widths=trace_widths,
        ))
    if len(trace) > _TRACE_TABLE_DISPLAY_LIMIT:
        lines.append(f"  ... {len(trace) - _TRACE_TABLE_DISPLAY_LIMIT} more row(s) in scoreboard.json")

    return "\n".join(lines) + "\n"
