"""§6 orchestration: turn Agent 1's residual into exceptions.csv + owner_queues.csv.

Pure functions of already-parsed rows -- no file I/O here (that lives in
``ledger_sense.routing.io``) and no import of ``ledger_sense.matching``
anything. A bank line's own ``candidates``/``features`` blob already carries
everything routing needs about it (top-candidate ledger id, amount/name/date
feature values) because Agent 1's ``feature_cell`` put it there for exactly
this reason -- routing never has to re-derive or re-score anything.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ledger_sense.config import config

from . import llm_classifier, roster
from .classify import classify_bank, classify_book, select_pairs
from .clock import compute as compute_clock
from .clock import parse_iso, severity_for, sla_hours_for
from .normalize import squash

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


def _json_cell(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _format_iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pass_id(*ids: str) -> str:
    for identifier in ids:
        if identifier:
            parts = identifier.split("-")
            if len(parts) >= 2:
                return parts[1]
    return ""


@dataclass
class Subject:
    """One row's worth of pre-assignment routing facts."""

    subject_kind: str
    bank_txn_id: str
    ledger_id: str
    category: str
    classification_detail: str
    match_status: str
    match_reason: str
    settlement_reason: str
    counterparty_key: str
    counterparty_label: str
    amount: Decimal
    currency: str
    inbound: bool
    opened_at: datetime
    evidence: dict = field(default_factory=dict)

    @property
    def exception_id(self) -> str:
        if self.subject_kind == "pair":
            return f"EXC-PAIR-{self.bank_txn_id}-{self.ledger_id}"
        if self.subject_kind == "bank":
            return f"EXC-BANK-{self.bank_txn_id}"
        return f"EXC-LEDGER-{self.ledger_id}"


def _bank_subject(row: dict, ledger_rows: dict, bank_rows: dict, subject_kind: str,
                   ledger_id: str) -> Subject:
    bank_txn_id = row["bank_txn_id"]
    bank = bank_rows[bank_txn_id]
    features = row["features"]
    category, detail = classify_bank(row["reason"], row["relation"], features)
    if subject_kind == "pair":
        detail = f"pair-and-suppress: ledger {ledger_id} unclaimed; {detail}"

    # W13: OpenAI routing fallback -- only classify_bank's rule 7
    # (unidentified_counterpart, "no earlier condition matched") is ever
    # eligible; rules 1-6 above are returned as-is and this branch never runs
    # for them (llm_classifier.apply_llm_fallback re-checks that marker
    # itself too, as defense in depth). Absent OPENAI_API_KEY,
    # config.openai_enabled() is False and this whole branch is skipped --
    # v1's rule-7 output is untouched (L18). The guardrail never reads this
    # category/detail or exceptions.csv at all, so it is unaffected (L21).
    llm_confidence = None
    if config.openai_enabled():
        category, detail, llm_confidence = llm_classifier.apply_llm_fallback(
            config, bank_txn_id, row["reason"], row["relation"], features, category, detail)

    if ledger_id and ledger_id in ledger_rows:
        counterparty_label = ledger_rows[ledger_id]["counterparty_name"]
    else:
        counterparty_label = bank["counterparty_name_raw"]
    counterparty_key = squash(counterparty_label)

    evidence = {
        "reason": row["reason"],
        "relation": row["relation"],
        "amount_class": features.get("amount"),
        "name_score": features.get("name"),
        "date_score": features.get("date"),
        "currency_score": features.get("currency"),
        "top_candidate_ledger_id": row["ledger_id"],
    }
    if llm_confidence is not None:
        evidence["llm_classified"] = True
        evidence["llm_confidence"] = str(llm_confidence)

    return Subject(
        subject_kind=subject_kind,
        bank_txn_id=bank_txn_id,
        ledger_id=ledger_id,
        category=category,
        classification_detail=detail,
        match_status=row["status"],
        match_reason=row["reason"],
        settlement_reason="",
        counterparty_key=counterparty_key,
        counterparty_label=counterparty_label,
        amount=bank["amount"],
        currency=bank["currency"],
        inbound=bank["direction"] == "credit",
        opened_at=parse_iso(bank["value_date"]),
        evidence=evidence,
    )


def _ledger_subject(ledger_id: str, settlement: dict, ledger_rows: dict) -> Subject:
    ledger = ledger_rows[ledger_id]
    category, detail = classify_book(settlement["reason"], settlement["ledger_amount"], settlement["residual"])
    counterparty_label = ledger["counterparty_name"]
    denominator = abs(settlement["ledger_amount"])
    ratio_pct = (abs(settlement["residual"]) / denominator * 100) if denominator != 0 else Decimal(100)

    evidence = {
        "settlement_reason": settlement["reason"],
        "n_parts": settlement["n_parts"],
        "ledger_amount": str(settlement["ledger_amount"]),
        "residual": str(settlement["residual"]),
        "residual_ratio_pct": str(ratio_pct),
    }

    return Subject(
        subject_kind="ledger",
        bank_txn_id="",
        ledger_id=ledger_id,
        category=category,
        classification_detail=detail,
        match_status="",
        match_reason="",
        settlement_reason=settlement["reason"],
        counterparty_key=squash(counterparty_label),
        counterparty_label=counterparty_label,
        amount=ledger["amount"],
        currency=ledger["currency"],
        inbound=ledger["amount"] > 0,
        opened_at=parse_iso(ledger["booked_at"]),
        evidence=evidence,
    )


def build_subjects(bank_outcomes: list, ledger_settlements: dict, ledger_rows: dict, bank_rows: dict) -> list:
    """Everything Agent 1 didn't close, as one flat list of :class:`Subject`."""
    unresolved = [row for row in bank_outcomes if row["status"] != "matched"]
    bank_top_candidate = {row["bank_txn_id"]: row["ledger_id"] for row in unresolved}
    unclaimed_ledger_ids = {lid for lid, s in ledger_settlements.items() if s["reason"] == "never_settled"}
    pairs = select_pairs(bank_top_candidate, unclaimed_ledger_ids)  # bank_txn_id -> ledger_id

    subjects = []
    for row in unresolved:
        bank_txn_id = row["bank_txn_id"]
        if bank_txn_id in pairs:
            subjects.append(_bank_subject(row, ledger_rows, bank_rows, "pair", pairs[bank_txn_id]))
        else:
            subjects.append(_bank_subject(row, ledger_rows, bank_rows, "bank", row["ledger_id"]))

    paired_ledger_ids = set(pairs.values())
    for ledger_id, settlement in ledger_settlements.items():
        if settlement["reason"] == "fully_settled" or ledger_id in paired_ledger_ids:
            continue
        subjects.append(_ledger_subject(ledger_id, settlement, ledger_rows))

    return subjects


def route(subjects: list, as_of: datetime) -> tuple[list, list]:
    """Assign owner + SLA clock to every subject, then render both output
    tables. Returns ``(exception_rows, queue_rows)`` as lists of dicts ready
    for ``write_csv``."""
    seen_ids = set()
    enriched = []
    for subject in subjects:
        exception_id = subject.exception_id
        if exception_id in seen_ids:
            raise ValueError(f"Duplicate exception_id: {exception_id}")
        seen_ids.add(exception_id)

        severity = severity_for(subject.category, subject.amount)
        sla_hours = sla_hours_for(subject.category, severity)
        clock = compute_clock(subject.opened_at, sla_hours, as_of)
        owner, basis = roster.assign(subject.category, subject.inbound, subject.counterparty_key)
        pass_id = _pass_id(subject.bank_txn_id, subject.ledger_id)

        enriched.append({
            "exception_id": exception_id,
            "pass_id": pass_id,
            "subject": subject,
            "severity": severity,
            "sla_hours": sla_hours,
            "clock": clock,
            "owner": owner,
            "assignment_basis": basis,
        })

    # §6.6.3: contiguous per-owner queue positions sorted by (due_at, exception_id).
    by_owner: dict = {}
    for item in enriched:
        by_owner.setdefault(item["owner"].owner_id, []).append(item)
    for owner_id, items in by_owner.items():
        items.sort(key=lambda item: (item["clock"].due_at, item["exception_id"]))
        for position, item in enumerate(items, start=1):
            item["queue_position"] = position

    exception_rows = []
    for item in sorted(enriched, key=lambda item: item["exception_id"]):
        subject, clock, owner = item["subject"], item["clock"], item["owner"]
        exception_rows.append({
            "exception_id": item["exception_id"],
            "pass_id": item["pass_id"],
            "subject_kind": subject.subject_kind,
            "bank_txn_id": subject.bank_txn_id,
            "ledger_id": subject.ledger_id,
            "category": subject.category,
            "classification_detail": subject.classification_detail,
            "match_status": subject.match_status,
            "match_reason": subject.match_reason,
            "settlement_reason": subject.settlement_reason,
            "counterparty_key": subject.counterparty_key,
            "counterparty_label": subject.counterparty_label,
            "amount": str(subject.amount),
            "currency": subject.currency,
            "severity": item["severity"],
            "owner_id": owner.owner_id,
            "owner_name": owner.owner_name,
            "owner_team": owner.owner_team,
            "assignment_basis": item["assignment_basis"],
            "opened_at": _format_iso(clock.opened_at),
            "sla_hours": str(item["sla_hours"]),
            "due_at": _format_iso(clock.due_at),
            "hours_remaining": str(clock.hours_remaining),
            "sla_state": clock.sla_state,
            "sla_display": clock.sla_display,
            "queue_position": item["queue_position"],
            "age_days": clock.age_days,
            "evidence": _json_cell(subject.evidence),
        })

    queue_rows = []
    for owner in roster.ROSTER:
        items = by_owner.get(owner.owner_id, [])
        due_ats = [item["clock"].due_at for item in items]
        queue_rows.append({
            "owner_id": owner.owner_id,
            "owner_name": owner.owner_name,
            "owner_team": owner.owner_team,
            "open_exceptions": len(items),
            "n_p1": sum(item["severity"] == "P1" for item in items),
            "n_p2": sum(item["severity"] == "P2" for item in items),
            "n_p3": sum(item["severity"] == "P3" for item in items),
            "earliest_due_at": _format_iso(min(due_ats)) if due_ats else "",
            "n_breached": sum(item["clock"].breached for item in items),
        })

    return exception_rows, queue_rows
