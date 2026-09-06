"""The close desk's seven actions (spec: BOARD.md TAPE-1 part C) -- ``pull``,
``analyze``, ``resolve``, ``promote``, ``next_close``, ``status``, ``logs``.
Every action returns an :class:`ActionResult` (what to print, plus a small
``data`` dict the caller may want, e.g. the example ``exception_id`` an
``analyze`` turn found) -- nothing here prints directly, so both the chat
loop and the one-shot CLI print identically.

Talks to Agents 1-5 only through their own published CLIs (``runner.run_module``,
i.e. ``python -m ledger_sense.<agent> ...``) or by reading a CSV/JSON file
they already wrote -- never by importing matching/routing internals (the
one explicit "must not" this card calls out). The one exception is
``ledger_sense.data``'s own helper modules (``dodo_source``, ``io_csv``,
``models``) for the ``dodo-cache`` fallback, since ``data/cli.py`` has no
``--source dodo-cache`` flag to shell out to (TAPE-1 part B is scoped to
``dodo_source.py`` itself, not a new CLI flag there) -- these are this
card's own files, not a foreign agent's scoring/routing internals.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

from ..config import load_config
from ..data.dodo_source import DEFAULT_CACHE_PATH, DodoAPIError, load_cached_dataset
from ..data.io_csv import write_csv
from ..data.models import BANK_COLUMNS, LEDGER_COLUMNS, MATCH_LINK_COLUMNS
from . import trace
from .paths import PassPaths
from .runner import run_module

DEFAULT_SEED = 1
DEFAULT_N_CASES = 200
MAX_N_CASES = 400  # spec: "synthetic --overlay n<=400"

# Mirrors DEMO.md's own pass-1/pass-2 convention: the *same* (seed, n_cases)
# across both passes is what makes "did it learn" a fair before/after
# comparison -- only pass_number differs.
DEMO_SEED = 42
DEMO_N_CASES = 300

DEFAULT_AS_OF = "2026-06-01T00:00:00Z"

_HTTP_CODE_RE = re.compile(r"HTTP (\d{3})")

RESOLVE_PREDICATE_FLAGS = (
    "--counterparty-key", "--currency", "--amount-delta-min",
    "--amount-delta-max", "--reference-transform", "--amount-class",
)


@dataclass
class ActionResult:
    ok: bool
    lines: list = field(default_factory=list)
    data: dict = field(default_factory=dict)


def _read_csv_rows(path) -> list:
    p = Path(path)
    if not p.is_file():
        return []
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write_bank_ledger_dataset(dataset, out_dir) -> None:
    """Mirrors ``data/cli.py::write_dataset`` exactly (small, deliberate
    duplication, same discipline this codebase already uses elsewhere --
    e.g. ``learning/predicate.py``'s own ``squash()`` -- rather than
    importing that CLI's argparse-wired ``main``)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(str(out_dir / "ledger.csv"), LEDGER_COLUMNS, dataset.ledger_rows)
    write_csv(str(out_dir / "bank.csv"), BANK_COLUMNS, dataset.bank_rows)
    write_csv(str(out_dir / "match_links.csv"), MATCH_LINK_COLUMNS, dataset.match_link_rows)


def _extract_http_code(stderr: str) -> Optional[str]:
    match = _HTTP_CODE_RE.search(stderr or "")
    return match.group(1) if match else None


def _infer_period_and_as_of(bank_csv_path) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """``(period_start, period_end, as_of)`` spanning every ``value_date`` in
    ``bank_csv_path`` -- deterministic, derived purely from the file on disk
    (never wall-clock, matching ``guardrail/period.py``'s own discipline).

    Without this, guardrail's default "calendar month containing --as-of"
    out_of_period window would reject an entire multi-month synthetic/demo
    batch just because no explicit period was given -- a human closing a
    real period always states its bounds; this infers the same thing from
    the data itself when the desk hasn't been told otherwise.
    """
    rows = _read_csv_rows(bank_csv_path)
    dates = sorted(row["value_date"] for row in rows if row.get("value_date"))
    if not dates:
        return None, None, None
    start = dates[0]
    end_dt = datetime.fromisoformat(dates[-1].replace("Z", "+00:00")).astimezone(timezone.utc) + timedelta(days=1)
    end = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return start, end, end


def _first_error_line(stderr: str) -> str:
    for line in (stderr or "").splitlines():
        if line.strip():
            return line.strip()
    return "error: (no message)"


# ---------------------------------------------------------------------------
# pull -- dodo live, else dodo-cache, else synthetic --overlay (n<=400)
# ---------------------------------------------------------------------------

def pull(
    paths: PassPaths,
    *,
    source: Optional[str] = None,
    seed: int = DEFAULT_SEED,
    n_cases: int = DEFAULT_N_CASES,
    cache_path: str = DEFAULT_CACHE_PATH,
) -> ActionResult:
    """``source`` is ``None`` for the desk's own auto fallback chain (dodo
    live -> dodo-cache -> synthetic), or an explicit ``"dodo"``/
    ``"dodo-cache"``/``"synthetic"`` to force exactly that source (no
    further fallback -- an explicit request that fails is reported as-is,
    never silently substituted)."""
    cfg = load_config()
    auto = source is None
    paths.dir.mkdir(parents=True, exist_ok=True)
    lines: list = []

    def counts():
        return len(_read_csv_rows(paths.bank_csv)), len(_read_csv_rows(paths.ledger_csv))

    # 1) dodo live (already correct -- W16; this card never re-touches it)
    if source in (None, "dodo"):
        if cfg.dodo_enabled():
            result = run_module("ledger_sense.data", [
                "--seed", str(seed), "--pass-number", "1", "--n-cases", str(n_cases),
                "--source", "dodo", "--out-dir", str(paths.dir),
            ])
            if result.ok:
                bank_n, ledger_n = counts()
                lines += ["source: dodo (live)", f"bank.csv rows={bank_n}; ledger.csv rows={ledger_n}"]
                return ActionResult(True, lines, {"source": "dodo"})
            if not auto:
                return ActionResult(False, [_first_error_line(result.stderr)])
            code = _extract_http_code(result.stderr)
            lines.append(f"live pull failed {code}; using labeled cache" if code
                         else "live pull failed; using labeled cache")
        elif not auto:
            return ActionResult(False, ["error: DODO_API_KEY is not set -- cannot use --source dodo"])
        # auto + no key configured: nothing was attempted, nothing to report -- fall through

    # 2) dodo-cache -- a fixed, checked-in, clearly-labeled snapshot
    if source in (None, "dodo-cache"):
        try:
            dataset = load_cached_dataset(cache_path, seed=seed)
        except DodoAPIError as exc:
            if not auto:
                return ActionResult(False, [f"error: {exc}"])
            # auto + no cache file either: fall through to synthetic
        else:
            _write_bank_ledger_dataset(dataset, paths.dir)
            lines += ["source: dodo-cache",
                      f"bank.csv rows={len(dataset.bank_rows)}; ledger.csv rows={len(dataset.ledger_rows)}"]
            return ActionResult(True, lines, {"source": "dodo-cache"})

    # 3) synthetic -- the default/explicit fallback, always n<=400
    capped_n = min(n_cases, MAX_N_CASES)
    result = run_module("ledger_sense.data", [
        "--seed", str(seed), "--pass-number", "1", "--n-cases", str(capped_n),
        "--overlay", "--out-dir", str(paths.dir),
    ])
    if not result.ok:
        return ActionResult(False, [_first_error_line(result.stderr)])
    bank_n, ledger_n = counts()
    lines += ["source: synthetic (overlay)", f"bank.csv rows={bank_n}; ledger.csv rows={ledger_n}"]
    return ActionResult(True, lines, {"source": "synthetic"})


# ---------------------------------------------------------------------------
# analyze -- matching + routing (+ guardrail, cheap) -> discrepancies
# ---------------------------------------------------------------------------

def analyze(paths: PassPaths, *, as_of: Optional[str] = None) -> ActionResult:
    if not paths.has_bank_data():
        return ActionResult(False, [f"error: no bank.csv/ledger.csv in {paths.dir} -- run 'pull' first"])

    period_start, period_end = None, None
    if as_of is None:
        period_start, period_end, inferred_as_of = _infer_period_and_as_of(paths.bank_csv)
        as_of = inferred_as_of or DEFAULT_AS_OF

    matching_result = run_module("ledger_sense.matching", [
        "--ledger", str(paths.ledger_csv), "--bank", str(paths.bank_csv),
        "--out-dir", str(paths.matching_out),
    ])
    if not matching_result.ok:
        return ActionResult(False, [f"error: matching failed -- {_first_error_line(matching_result.stderr)}"])

    # Guardrail is cheap and deterministic -- run it for the bonus allow/
    # block/hold context, but its own output is not required by routing, so
    # a failure here is reported but never aborts the turn.
    guardrail_args = [
        "--ledger", str(paths.ledger_csv), "--bank", str(paths.bank_csv),
        "--outcomes", str(paths.outcomes_csv()), "--settlements", str(paths.settlements_csv()),
        "--as-of", as_of, "--out-dir", str(paths.guardrail_out),
    ]
    if period_start and period_end:
        guardrail_args += ["--period-start", period_start, "--period-end", period_end]
    guardrail_result = run_module("ledger_sense.guardrail", guardrail_args)

    routing_result = run_module("ledger_sense.routing", [
        "--outcomes", str(paths.outcomes_csv()), "--settlements", str(paths.settlements_csv()),
        "--ledger", str(paths.ledger_csv), "--bank", str(paths.bank_csv),
        "--as-of", as_of, "--out-dir", str(paths.routing_out),
    ])
    if not routing_result.ok:
        return ActionResult(False, [f"error: routing failed -- {_first_error_line(routing_result.stderr)}"])

    outcome_rows = _read_csv_rows(paths.outcomes_csv())
    exception_rows = _read_csv_rows(paths.exceptions_csv())
    total = len(exception_rows)
    counts = Counter(row["category"] for row in exception_rows)
    top = counts.most_common(3)
    example_id = exception_rows[0]["exception_id"] if exception_rows else None

    lines = [
        f"bank lines={len(outcome_rows)}",
        f"exceptions={total}",
        "top classes: " + (", ".join(f"{name}={n}" for name, n in top) if top else "(none)"),
    ]
    if guardrail_result.ok:
        verdict_lines = [line for line in guardrail_result.stdout.splitlines()
                          if line.startswith(("allow:", "block:", "hold:"))]
        if verdict_lines:
            lines.append("guardrail: " + "; ".join(verdict_lines))
    if example_id:
        lines.append(f"example exception_id: {example_id}")
    lines.append("discrepancies ready")

    data = {
        "example_exception_id": example_id,
        "bank_lines": len(outcome_rows),
        "exceptions_total": total,
        "files": [str(paths.outcomes_csv()), str(paths.exceptions_csv())],
    }
    return ActionResult(True, lines, data)


# ---------------------------------------------------------------------------
# resolve -- delegates entirely to `ledger_sense resolve` (the real CLI)
# ---------------------------------------------------------------------------

def resolve(
    paths: PassPaths,
    *,
    exception_ref: str,
    resolution_type: str,
    predicate_flags: dict,
    rationale: str,
    resolved_by: str = "desk-operator",
    resolved_at: Optional[str] = None,
    as_of: str = DEFAULT_AS_OF,
) -> ActionResult:
    """``exception_ref`` is either a real ``exception_id`` or the literal
    ``"that one"``/``"that"`` -- resolved against the most recent ``analyze``
    turn's example id (this session's in-memory state if available, else
    ``demo_trace.json`` on disk -- so it works across separate one-shot
    invocations too)."""
    if exception_ref.strip().lower() in ("that", "that one"):
        exception_id = trace.last_example_exception_id(paths.trace_path)
        if not exception_id:
            return ActionResult(False, [
                "error: no prior 'analyze' example exception_id in this session -- "
                "run 'analyze' first, or give an explicit exception_id"
            ])
    else:
        exception_id = exception_ref

    if not paths.exceptions_csv().is_file() or not paths.outcomes_csv().is_file():
        return ActionResult(False, [f"error: no exceptions/outcomes found under {paths.dir} -- run 'analyze' first"])

    resolved_at = resolved_at or as_of
    argv = [
        "resolve",
        "--exceptions", str(paths.exceptions_csv()),
        "--outcomes", str(paths.outcomes_csv()),
        "--exception-id", exception_id,
        "--resolution-type", resolution_type,
        "--rationale", rationale,
        "--resolved-by", resolved_by,
        "--resolved-at", resolved_at,
        "--candidates", str(paths.candidates_json),
    ]
    for flag, value in predicate_flags.items():
        if value is not None and value != "":
            argv += [flag, str(value)]

    result = run_module("ledger_sense.learning", argv)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not result.ok:
        lines += [line for line in result.stderr.splitlines() if line.strip()]
        return ActionResult(False, lines)
    rule_id = next((line.split("=", 1)[1] for line in lines if line.startswith("rule_id=")), None)
    return ActionResult(True, lines, {"exception_id": exception_id, "rule_id": rule_id})


# ---------------------------------------------------------------------------
# promote -- delegates entirely to `ledger_sense promote` (the real CLI);
# this is the ONLY path that may ever write rules.json (law L14).
# ---------------------------------------------------------------------------

def promote(
    paths: PassPaths,
    *,
    rule_id: str,
    confirm: str,
    promoted_by: str = "desk-operator",
    promoted_at: Optional[str] = None,
    as_of: str = DEFAULT_AS_OF,
) -> ActionResult:
    argv = [
        "promote", rule_id,
        "--confirm", confirm,
        "--promoted-by", promoted_by,
        "--promoted-at", promoted_at or as_of,
        "--rules", str(paths.rules_json),
        "--candidates", str(paths.candidates_json),
    ]
    result = run_module("ledger_sense.learning", argv)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not result.ok:
        lines += [line for line in result.stderr.splitlines() if line.strip()]
        return ActionResult(False, lines)
    return ActionResult(True, lines)


# ---------------------------------------------------------------------------
# next_close -- pass 2 (generate if missing) -> rules off vs on -> did it learn
# ---------------------------------------------------------------------------

def next_close(
    pass1: PassPaths,
    pass2: PassPaths,
    *,
    seed: int = DEMO_SEED,
    n_cases: int = DEMO_N_CASES,
    as_of: Optional[str] = None,
) -> ActionResult:
    lines: list = []

    if not pass2.has_bank_data():
        capped_n = min(n_cases, MAX_N_CASES)
        gen = run_module("ledger_sense.data", [
            "--seed", str(seed), "--pass-number", "2", "--n-cases", str(capped_n),
            "--overlay", "--out-dir", str(pass2.dir),
        ])
        if not gen.ok:
            return ActionResult(False, [f"error: pass-2 generation failed -- {_first_error_line(gen.stderr)}"])
        lines.append(f"generated pass 2 data in {pass2.dir} (seed={seed}, n_cases={capped_n}, overlay)")

    period_start, period_end = None, None
    if as_of is None:
        period_start, period_end, inferred_as_of = _infer_period_and_as_of(pass2.bank_csv)
        as_of = inferred_as_of or DEFAULT_AS_OF

    match_result = run_module("ledger_sense.matching", [
        "--ledger", str(pass2.ledger_csv), "--bank", str(pass2.bank_csv), "--out-dir", str(pass2.matching_out),
    ])
    if not match_result.ok:
        return ActionResult(False, [f"error: pass-2 matching failed -- {_first_error_line(match_result.stderr)}"])

    off_routing = run_module("ledger_sense.routing", [
        "--outcomes", str(pass2.outcomes_csv()), "--settlements", str(pass2.settlements_csv()),
        "--ledger", str(pass2.ledger_csv), "--bank", str(pass2.bank_csv),
        "--as-of", as_of, "--out-dir", str(pass2.routing_off_out),
    ])
    if not off_routing.ok:
        return ActionResult(False, [f"error: pass-2 routing (rules off) failed -- {_first_error_line(off_routing.stderr)}"])
    off_counts = Counter(row["category"] for row in _read_csv_rows(pass2.routing_off_out / "exceptions.csv"))

    rule_hits: list = []
    if not pass1.rules_json.is_file():
        lines.append(f"no rules.json yet at {pass1.rules_json} (nothing promoted) -- pass 2 run with zero learned rules")
        on_counts = off_counts
    else:
        apply_args = [
            "apply-rules",
            "--outcomes", str(pass2.outcomes_csv()), "--settlements", str(pass2.settlements_csv()),
            "--ledger", str(pass2.ledger_csv), "--bank", str(pass2.bank_csv),
            "--rules", str(pass1.rules_json), "--as-of", as_of, "--out-dir", str(pass2.applied_out),
        ]
        if period_start and period_end:
            apply_args += ["--period-start", period_start, "--period-end", period_end]
        apply_result = run_module("ledger_sense.learning", apply_args)
        if not apply_result.ok:
            return ActionResult(False, [f"error: apply-rules failed -- {_first_error_line(apply_result.stderr)}"])
        rule_hits = _read_csv_rows(pass2.applied_out / "rule_hits.csv")

        on_routing = run_module("ledger_sense.routing", [
            "--outcomes", str(pass2.applied_out / "match_outcomes.csv"),
            "--settlements", str(pass2.applied_out / "ledger_settlements.csv"),
            "--ledger", str(pass2.ledger_csv), "--bank", str(pass2.bank_csv),
            "--as-of", as_of, "--out-dir", str(pass2.routing_on_out),
        ])
        if not on_routing.ok:
            return ActionResult(False, [f"error: pass-2 routing (rules on) failed -- {_first_error_line(on_routing.stderr)}"])
        on_counts = Counter(row["category"] for row in _read_csv_rows(pass2.routing_on_out / "exceptions.csv"))

    lines.append("class before -> after (rules off -> on):")
    for category in sorted(set(off_counts) | set(on_counts)):
        before, after = off_counts.get(category, 0), on_counts.get(category, 0)
        marker = " (dropped)" if after < before else ""
        lines.append(f"  {category}: {before} -> {after}{marker}")
    lines.append(f"rule_hits: {len(rule_hits)}")
    lines.append(f"trace: {pass2.trace_path}")

    data = {
        "off_counts": dict(off_counts), "on_counts": dict(on_counts), "rule_hits": len(rule_hits),
        "files": [str(pass2.routing_off_out / "exceptions.csv"), str(pass2.routing_on_out / "exceptions.csv")],
    }
    return ActionResult(True, lines, data)


# ---------------------------------------------------------------------------
# status / logs
# ---------------------------------------------------------------------------

def status(pass1: PassPaths, pass2: PassPaths) -> ActionResult:
    lines = [
        f"pass1 dir: {pass1.dir} (bank data: {'yes' if pass1.has_bank_data() else 'no'})",
        f"pass2 dir: {pass2.dir} (bank data: {'yes' if pass2.has_bank_data() else 'no'})",
        f"rules.json: {'present' if pass1.rules_json.is_file() else 'absent'} ({pass1.rules_json})",
    ]
    if pass1.exceptions_csv().is_file():
        lines.append(f"pass1 exceptions: {len(_read_csv_rows(pass1.exceptions_csv()))}")
    else:
        lines.append("pass1 exceptions: not analyzed yet")
    return ActionResult(True, lines)


def logs(paths: PassPaths) -> ActionResult:
    entries = trace.read_entries(paths.trace_path)
    if not entries:
        return ActionResult(True, [f"no demo_trace.json yet at {paths.trace_path}"])
    last = entries[-1]
    lines = [f"demo_trace.json: {len(entries)} turn(s) recorded at {paths.trace_path}"]
    duration = last.get("duration_seconds")
    duration_str = f"{duration:.3f}s" if isinstance(duration, (int, float)) else "?"
    lines.append(f"last turn: command={last.get('command')} duration={duration_str} at {last.get('timestamp')}")
    trace_id = last.get("neatlogs_trace_id")
    lines.append(f"neatlogs trace id: {trace_id}" if trace_id
                 else "neatlogs trace id: none (tracing disabled, or no span sent this turn)")
    return ActionResult(True, lines)
