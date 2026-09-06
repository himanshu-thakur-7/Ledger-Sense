"""``demo_trace.json`` -- one JSON array, appended to on every desk turn
(spec: BOARD.md TAPE-1 part C, "Write demo_trace.json every turn (agent,
command, files, duration)."). The wall-clock ``timestamp`` field here is
pure observability bookkeeping about *when a demo turn actually ran* -- it
is never read back as a business-logic instant. Every ``--as-of`` this
package computes anything against stays an explicit, human/caller-given
value (matching the rest of this codebase's "never wall-clock" discipline).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def append_entry(
    trace_path,
    *,
    command: str,
    files: list,
    duration_seconds: float,
    example_exception_id: Optional[str] = None,
    neatlogs_trace_id: Optional[str] = None,
    ok: bool = True,
) -> dict:
    """Append one turn record and rewrite ``trace_path`` -- never raises;
    a corrupt/unreadable existing file is treated as an empty history
    rather than blocking the desk (L18's spirit: tracing/logging must never
    be the thing that breaks a turn)."""
    entry: dict[str, Any] = {
        "agent": "operator",
        "command": command,
        "files": [str(f) for f in files],
        "duration_seconds": duration_seconds,
        "ok": ok,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if example_exception_id is not None:
        entry["example_exception_id"] = example_exception_id
    if neatlogs_trace_id is not None:
        entry["neatlogs_trace_id"] = neatlogs_trace_id

    try:
        entries = read_entries(trace_path)
        entries.append(entry)
        path = Path(trace_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return entry


def read_entries(trace_path) -> list:
    path = Path(trace_path)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def last_example_exception_id(trace_path) -> Optional[str]:
    """The most recent turn's recorded example exception id, if any -- what
    ``resolve that one`` resolves against."""
    for entry in reversed(read_entries(trace_path)):
        candidate = entry.get("example_exception_id")
        if candidate:
            return candidate
    return None
