"""The close desk's turn dispatcher and REPL loop (spec: BOARD.md TAPE-1
part C). One :class:`Desk` instance holds the two working pass directories
for a session; ``dispatch_line`` is the single place both the interactive
``chat`` loop and the one-shot free-text order (``ledger-sense-desk "..."``)
go through, so both entry modes behave identically for the same text.
"""

from __future__ import annotations

import sys
import time
from typing import IO

from ..config import load_config
from . import actions, trace
from .intents import Intent, classify
from .paths import PassPaths

PROMPT = "desk> "

_UNDERSTOOD_HELP = (
    "desk: didn't understand that -- try: pull, analyze, resolve <id|that one> <type> "
    "[--flags] [\"rationale\"], promote <rule_id> yes-always, next close, status, logs, quit"
)


def _call(desk: "Desk", intent: Intent) -> actions.ActionResult:
    if intent.name == "pull":
        return actions.pull(desk.pass1)
    if intent.name == "analyze":
        return actions.analyze(desk.pass1)
    if intent.name == "resolve":
        args = intent.args
        return actions.resolve(
            desk.pass1,
            exception_ref=args["exception_ref"],
            resolution_type=args["resolution_type"],
            predicate_flags=args["predicate_flags"],
            rationale=args["rationale"] or "",
            resolved_by=args.get("resolved_by") or "desk-operator",
            resolved_at=args.get("resolved_at"),
        )
    if intent.name == "promote":
        return actions.promote(desk.pass1, rule_id=intent.args["rule_id"], confirm=intent.args["confirm"])
    if intent.name == "next_close":
        return actions.next_close(desk.pass1, desk.pass2)
    if intent.name == "status":
        return actions.status(desk.pass1, desk.pass2)
    if intent.name == "logs":
        return actions.logs(desk.pass1)
    return actions.ActionResult(False, [f"desk: unknown intent {intent.name!r}"])


class Desk:
    def __init__(self, dir_path, pass2_dir_path) -> None:
        self.pass1 = PassPaths(dir_path)
        self.pass2 = PassPaths(pass2_dir_path)
        self.cfg = load_config()

    def run_intent(self, intent: Intent, out: IO[str]) -> actions.ActionResult:
        start = time.monotonic()
        try:
            result = _call(self, intent)
        except Exception as exc:
            # A single turn's own failure must never take the desk down
            # (L18's spirit, extended to this interactive shell).
            duration = time.monotonic() - start
            result = actions.ActionResult(False, [f"desk: {intent.name} failed -- {exc}"])
            for line in result.lines:
                print(line, file=out)
            trace.append_entry(self.pass1.trace_path, command=intent.name, files=[],
                                duration_seconds=duration, ok=False)
            return result

        duration = time.monotonic() - start
        for line in result.lines:
            print(line, file=out)
        trace.append_entry(
            self.pass1.trace_path, command=intent.name, files=result.data.get("files", []),
            duration_seconds=duration, example_exception_id=result.data.get("example_exception_id"),
            ok=result.ok,
        )
        return result

    def dispatch_line(self, text: str, out: IO[str]) -> bool:
        """Runs every intent found in ``text`` in order; returns ``False``
        only when the line included ``quit``/``exit`` (everything else on
        that same line still runs first)."""
        intents = classify(text, self.cfg)
        if not intents:
            print(_UNDERSTOOD_HELP, file=out)
            return True
        keep_going = True
        for intent in intents:
            if intent.name == "quit":
                print("desk: bye", file=out)
                keep_going = False
                continue
            self.run_intent(intent, out)
        return keep_going

    def chat(self, in_stream: IO[str] = sys.stdin, out: IO[str] = sys.stdout) -> int:
        while True:
            print(PROMPT, end="", flush=True, file=out)
            line = in_stream.readline()
            if line == "":  # EOF -- no explicit quit typed
                print(file=out)
                break
            if not self.dispatch_line(line, out):
                break
        return 0
