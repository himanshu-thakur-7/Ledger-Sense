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
