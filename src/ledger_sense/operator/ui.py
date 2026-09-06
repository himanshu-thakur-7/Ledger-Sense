"""Presentation-only terminal styling for the close desk (CARD UI-1).

Every literal token another script/test greps for -- ``"discrepancies
ready"``, ``"status=candidate"``, ``"resolved by rule:"``, ``"rule_hits:"``,
``"desk>"``, ``"yes-always"``, ``"class before -> after ..."``, ``"source:
dodo (live)"`` / ``"source: dodo-cache"`` / ``"source: synthetic
(overlay)"``, ``"neatlogs trace id:"`` -- travels through this module
completely unmodified: color codes, when enabled, wrap AROUND a line's
existing text, they never split, replace, or reflow it. Nothing in here
changes a single character of ``ActionResult.lines`` -- callers print the
*original* line, and separately may print extra decoration (a table) this
module builds fresh from that same data.

All styling -- color, tables, and the pull/analyze/next-close spinner --
auto-disables the moment stdout is not a real terminal, or ``NO_COLOR`` is
set (https://no-color.org, "presence... regardless of value"). That is
what keeps ``scripts/record_demo.sh`` (piped through ``tee``/command
substitution -- never a tty) and every existing test's exact-stdout
assertions passing unmodified.

Nothing here can take the desk down (law L18): every entry point below
degrades to "no styling" rather than raising.
"""

from __future__ import annotations

import itertools
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from typing import IO, Iterable, List, Optional, Sequence, Tuple

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"

PROMPT_STYLE = (BOLD, CYAN)


def styling_enabled(stream: Optional[IO[str]] = None) -> bool:
    """Whether color/tables/spinner may be used on ``stream`` (default
    ``sys.stdout``). Never raises -- any doubt (a stream with no
    ``isatty``, a closed pipe, ...) resolves to ``False``."""
    try:
        if "NO_COLOR" in os.environ:
            return False
        target = sys.stdout if stream is None else stream
        return bool(target.isatty())
    except Exception:
        return False


def colorize(text: str, *codes: str, enabled: bool) -> str:
    """Wraps ``text`` in ANSI ``codes`` -- verbatim, never split -- and a
    trailing reset. A no-op (returns ``text`` unchanged) when ``enabled``
    is falsy or no codes are given."""
    if not enabled or not codes or not text:
        return text
    return "".join(codes) + text + RESET


# ---------------------------------------------------------------------------
# Whole-line styling -- pattern -> ANSI codes, applied to the FULL line so
# no grepped substring is ever touched, only wrapped.
# ---------------------------------------------------------------------------

_LINE_STYLES: Tuple[Tuple[re.Pattern, Tuple[str, ...]], ...] = (
    (re.compile(r"^error:"), (BOLD, RED)),
    (re.compile(r"^desk: .* failed --"), (BOLD, RED)),
    (re.compile(r"^desk: didn't understand"), (YELLOW,)),
    (re.compile(r"^desk: bye$"), (CYAN,)),
    (re.compile(r"^live pull failed"), (YELLOW,)),
    (re.compile(r"^source: dodo \(live\)"), (BOLD, GREEN)),
    (re.compile(r"^source: dodo-cache"), (YELLOW,)),
    (re.compile(r"^source: synthetic \(overlay\)"), (CYAN,)),
    (re.compile(r"^discrepancies ready$"), (BOLD, GREEN)),
    (re.compile(r"^top classes:"), (BOLD,)),
    (re.compile(r"^guardrail:"), (MAGENTA,)),
    (re.compile(r"^example exception_id:"), (CYAN,)),
    (re.compile(r"^status=candidate$"), (BOLD, GREEN)),
    (re.compile(r"^status=resolved"), (BOLD, GREEN)),
    (re.compile(r"^(resolution_id|exception_id|resolution_type|rule_id)="), (CYAN,)),
    (re.compile(r"^\S+ <- RES-"), (BOLD, GREEN)),
    (re.compile(r"^class before -> after"), (BOLD,)),
    (re.compile(r"^rule_hits: 0$"), (YELLOW,)),
    (re.compile(r"^rule_hits: "), (BOLD, GREEN)),
    (re.compile(r"^resolved by rule: 0$"), (YELLOW,)),
    (re.compile(r"^resolved by rule: "), (BOLD, GREEN)),
    (re.compile(r"^rules\.json: absent"), (YELLOW,)),
    (re.compile(r"^rules\.json: present"), (GREEN,)),
    (re.compile(r"^no rules\.json yet"), (YELLOW,)),
    (re.compile(r"^trace: "), (DIM,)),
    (re.compile(r"^neatlogs trace id: none"), (DIM,)),
    (re.compile(r"^neatlogs trace id:"), (CYAN,)),
)


def _style_line(line: str, enabled: bool) -> str:
    if not enabled:
        return line
    for pattern, codes in _LINE_STYLES:
        if pattern.search(line):
            return colorize(line, *codes, enabled=True)
    return line


# ---------------------------------------------------------------------------
# Tables -- pure decoration ADDED after the original (styled) line(s), so
# the plain literal line a script/test greps for is always still there,
# byte-for-byte, on its own line.
# ---------------------------------------------------------------------------

def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]], *, enabled: bool) -> List[str]:
    """A small box-drawn table. Falls back to returning ``[]`` (nothing to
    add) if there is nothing to show -- callers already printed the plain
    line, so a table is purely additive."""
    if not rows:
        return []
    try:
        widths = [
            max(len(str(headers[i])), *(len(str(row[i])) for row in rows))
            for i in range(len(headers))
        ]

        def fmt_row(cells: Sequence[str]) -> str:
            padded = [f" {str(c).ljust(w)} " for c, w in zip(cells, widths)]
            return colorize("│", DIM, enabled=enabled) + colorize("│", DIM, enabled=enabled).join(padded) + colorize("│", DIM, enabled=enabled)

        def rule(left: str, mid: str, right: str) -> str:
            return colorize(left + mid.join("─" * (w + 2) for w in widths) + right, DIM, enabled=enabled)

        lines = [rule("┌", "┬", "┐"), fmt_row([colorize(h, BOLD, enabled=enabled) for h in headers]), rule("├", "┼", "┤")]
        lines += [fmt_row(row) for row in rows]
        lines.append(rule("└", "┴", "┘"))
        return ["  " + line for line in lines]
    except Exception:
        # A cosmetic table is never worth taking the desk down over (L18).
        return []


_TOP_CLASSES_RE = re.compile(r"^top classes: (.+)$")
_CLASS_DELTA_HEADER = "class before -> after (rules off -> on):"
_CLASS_DELTA_ROW_RE = re.compile(r"^  (\S+): (\d+) -> (\d+)(?: \(dropped\))?$")


def _delta_cell(before: str, after: str, enabled: bool) -> str:
    delta = int(after) - int(before)
    text = f"{delta:+d}"
    if delta < 0:
        return colorize(text, GREEN, enabled=enabled)
    if delta > 0:
        return colorize(text, RED, enabled=enabled)
    return colorize(text, DIM, enabled=enabled)


def format_lines(lines: Iterable[str], *, enabled: bool) -> List[str]:
    """The single entry point both entry modes render ``ActionResult.lines``
    through. Returns the lines unchanged (as a new list) when ``enabled``
    is falsy -- the exact behavior every existing plain-text test asserts
    on. When enabled, every original line is still emitted (only wrapped
    in ANSI codes, never split), with tables appended as pure extras for
    ``top classes:`` and the class-delta block.
    """
    lines = list(lines)
    if not enabled:
        return lines

    out: List[str] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]

        top_match = _TOP_CLASSES_RE.match(line)
        if top_match and top_match.group(1) != "(none)":
            out.append(_style_line(line, enabled))
            rows = []
            for part in top_match.group(1).split(", "):
                if "=" in part:
                    name, _, count = part.partition("=")
                    rows.append((name, count))
            out.extend(render_table(("class", "count"), rows, enabled=enabled))
            i += 1
            continue

        if line == _CLASS_DELTA_HEADER:
            out.append(_style_line(line, enabled))
            i += 1
            rows = []
            while i < n:
                row_match = _CLASS_DELTA_ROW_RE.match(lines[i])
                if not row_match:
                    break
                out.append(_style_line(lines[i], enabled))
                name, before, after = row_match.groups()
                rows.append((name, before, after, _delta_cell(before, after, enabled)))
                i += 1
            out.extend(render_table(("class", "before", "after", "Δ"), rows, enabled=enabled))
            continue

        out.append(_style_line(line, enabled))
        i += 1
    return out


# ---------------------------------------------------------------------------
# Spinner -- a progress indicator for the pull/analyze/next-close subprocess
# calls (they shell out and can take a moment). Runs on a daemon thread
# purely to animate while the caller blocks on a synchronous call; always
# clears itself before returning so it never leaves stray characters ahead
# of the turn's own result lines.
# ---------------------------------------------------------------------------

class _Spinner:
    _FRAMES = "|/-\\"

    def __init__(self, message: str, stream: IO[str]) -> None:
        self._message = message
        self._stream = stream
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            for frame in itertools.cycle(self._FRAMES):
                if self._stop.is_set():
                    break
                print(f"\r{self._message} {frame}", end="", flush=True, file=self._stream)
                self._stop.wait(0.1)
        except Exception:
            pass  # a broken spinner must never take the desk down (L18)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        try:
            blank = " " * (len(self._message) + 2)
            print(f"\r{blank}\r", end="", flush=True, file=self._stream)
        except Exception:
            pass


@contextmanager
def spinner(message: str, *, enabled: bool, stream: Optional[IO[str]] = None):
    """No-op context manager when ``enabled`` is falsy (non-tty, NO_COLOR,
    or anything that makes ``styling_enabled`` return False) -- the caller
    always gets the exact same return value either way, only the terminal
    chrome differs."""
    if not enabled:
        yield
        return
    target = sys.stdout if stream is None else stream
    spin = _Spinner(message, target)
    try:
        spin.start()
        yield
    finally:
        spin.stop()
