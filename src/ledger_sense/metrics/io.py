"""Strict, read-only CSV/JSON boundary onto Agents 1-4's own output files, plus
the one file law L2 reserves for this agent alone: ``match_links.csv``.

Every column list below is this package's own copy of an upstream agent's
published schema (matching's §5.8, routing's §6.7, guardrail's §8.2,
learning's rule_hits.csv, the §4 generator's MatchLink table) -- duplicated
here exactly, never imported, the same discipline
``ledger_sense.routing.io``/``ledger_sense.guardrail.csv_io``/
``ledger_sense.learning.io`` already follow for their own upstream reads (law
L1). Nothing in this module imports ``ledger_sense.matching``,
``ledger_sense.routing``, ``ledger_sense.guardrail``, or
``ledger_sense.learning``.

Every reader refuses loudly (``MetricsInputError``) on a missing file or a
header that doesn't match -- the spec's "never fabricate a number" applies to
inputs too: a shifted or absent column must stop the run, not get silently
misread.
"""

import csv
import json
from pathlib import Path

# Agent 1 (matching) -- spec §5.8, duplicated.
OUTCOME_COLUMNS = [
    "bank_txn_id", "status", "relation", "ledger_id", "tier", "score", "margin", "reason",
    "reason_detail", "matched_amount", "residual_after", "candidates", "features",
    "llm_model", "llm_confidence", "llm_is_stub",
]
SETTLEMENT_COLUMNS = [
    "ledger_id", "ledger_amount", "matched_amount", "residual", "n_parts", "bank_txn_ids",
    "fully_settled", "reason",
]

# Agent 2 (routing) -- spec §6.7, duplicated.
EXCEPTION_COLUMNS = [
    "exception_id", "pass_id", "subject_kind", "bank_txn_id", "ledger_id",
    "category", "classification_detail", "match_status", "match_reason",
    "settlement_reason", "counterparty_key", "counterparty_label",
    "amount", "currency", "severity", "owner_id", "owner_name", "owner_team",
    "assignment_basis", "opened_at", "sla_hours", "due_at",
    "hours_remaining", "sla_state", "sla_display", "queue_position",
    "age_days", "evidence",
]
QUEUE_COLUMNS = [
    "owner_id", "owner_name", "owner_team", "open_exceptions",
    "n_p1", "n_p2", "n_p3", "earliest_due_at", "n_breached",
]

# Agent 4 (guardrail) -- spec §8.2, duplicated.
RELEASE_COLUMNS = [
    "bank_txn_id", "verdict", "primary_rule", "all_firing_rules", "reason",
    "upstream_context", "required_approvals", "policy_version",
]
AUDIT_COLUMNS = ["bank_txn_id", "rule", "verdict", "detail", "policy_version"]

# Agent 3 (learning) -- rule_hits.csv, duplicated from ledger_sense.learning.apply.RULE_HIT_COLUMNS.
RULE_HIT_COLUMNS = [
    "bank_txn_id", "ledger_id", "rule_id", "resolution_id", "resolution_type",
    "applied_cents", "guardrail_verdict", "predicate",
]

# §4 generator's ground-truth MatchLink table, duplicated from
# ledger_sense.data.models.MATCH_LINK_COLUMNS. Law L2: this is the only agent
# allowed to read it.
MATCH_LINK_COLUMNS = ["ledger_id", "bank_txn_id", "relation", "defect", "case_id", "note"]


class MetricsInputError(ValueError):
    """A required scoreboard input is missing, unreadable, or malformed --
    refused rather than papered over (spec: never fabricate a number)."""


def _require(path) -> Path:
    p = Path(path)
    if not p.exists():
        raise MetricsInputError(f"required file not found: {path}")
    if not p.is_file():
        raise MetricsInputError(f"expected a file, not a directory: {path}")
    return p


def _rows(path, columns) -> list:
    p = _require(path)
    with p.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != columns:
            raise MetricsInputError(f"Unexpected columns in {path}: {reader.fieldnames}")
        rows = []
        for row in reader:
            if any(value is None for value in row.values()):
                raise MetricsInputError(f"Malformed CSV row in {path}")
            rows.append(row)
        return rows


def read_outcomes(path) -> list:
    """Raw ``match_outcomes.csv`` rows, as plain dicts (strings, un-parsed)."""
    return _rows(path, OUTCOME_COLUMNS)


def read_outcome_features(path) -> dict:
    """``bank_txn_id -> parsed features dict``, for exception-class shaping."""
    return {
        row["bank_txn_id"]: (json.loads(row["features"]) if row["features"] else {})
        for row in _rows(path, OUTCOME_COLUMNS)
    }


def read_settlements(path) -> list:
    return _rows(path, SETTLEMENT_COLUMNS)


def read_exceptions(path) -> list:
    return _rows(path, EXCEPTION_COLUMNS)


def read_queues(path) -> list:
    return _rows(path, QUEUE_COLUMNS)


def read_release_decisions(path) -> list:
    return _rows(path, RELEASE_COLUMNS)


def read_guardrail_audit(path) -> list:
    return _rows(path, AUDIT_COLUMNS)


def read_rule_hits(path) -> list:
    return _rows(path, RULE_HIT_COLUMNS)


def read_match_links(path) -> list:
    """Law L2: the ground-truth table only Agent 5 may read."""
    return _rows(path, MATCH_LINK_COLUMNS)


def read_rules(path) -> list:
    p = _require(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MetricsInputError(f"{path} is not valid JSON: {exc}") from exc
    if "rules" not in data:
        raise MetricsInputError(f"{path} is missing the 'rules' key")
    return data["rules"]
