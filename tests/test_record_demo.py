"""Slow, opt-in end-to-end check for TAPE-2.1 acceptance #1:
``scripts/record_demo.sh`` must exit 0 in well under two minutes AND
actually prove learning -- real ``rule_hits>0``, real ``resolved by rule:
N>0`` -- not just print the right words. Runs the real script against the
real repo (L20's one deliberate real-subprocess exception, same discipline
as this suite's other ``@pytest.mark.slow`` tests); PART A's own keyless
env handling inside the script means this needs no live keys either.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "record_demo.sh"


@pytest.mark.slow
def test_record_demo_exits_zero_and_proves_learning():
    result = subprocess.run(
        ["bash", str(SCRIPT)], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout  # the script merges its own stderr into this same stream

    for phrase in ("desk>", "discrepancies ready", "status=candidate", "yes-always",
                   "resolved by rule", "class delta"):
        assert phrase in out, f"required phrase missing from record_demo.sh stdout: {phrase!r}"

    hits = re.search(r"^rule_hits: (\d+)$", out, re.MULTILINE)
    assert hits and int(hits.group(1)) > 0, "'next close' reported rule_hits<=0 -- tape didn't learn"

    resolved = re.search(r"^resolved by rule: (\d+)$", out, re.MULTILINE)
    assert resolved and int(resolved.group(1)) > 0, "apply-rules reported 'resolved by rule: 0'"

    assert hits.group(1) == resolved.group(1), (
        "desk's own rule_hits and apply-rules' resolved-by-rule receipt disagree"
    )
