"""Extra checks for shape C: handoff boundary, secret/PII leaks, idempotency, crash."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab8.config_loader import repo_path
from lab8.db import connect, load_jsonl
from lab8.durability import InjectedCrash, committed_audit_count, get_refund
from lab8.handoff import ALLOWED_FIELDS, FACT_KEYS, READONLY_TOOLS, WRITE_TOOLS
from lab8.shapes.orchestrator import run_claim

CLAIMS = load_jsonl(repo_path("data", "public_claims.jsonl"))
BY_ID = {row["claim_id"]: row for row in CLAIMS}


def _run_files(result) -> list[Path]:
    """Trace plus every `<run_id>*` file in the same directory (what the grader scans)."""
    trace = Path(result.trace_path)
    return sorted(trace.parent.glob(f"{result.run_id}*"))


def _events(result) -> list[dict]:
    body = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
    assert body["run_id"] == result.run_id
    assert isinstance(body["events"], list)
    return body["events"]


@pytest.mark.parametrize("claim_id", [row["claim_id"] for row in CLAIMS])
def test_orchestrator_leaks_no_secret_or_pii(public_db, claim_id):
    row = BY_ID[claim_id]
    result = run_claim(claim_id, seed=11, store_path=str(public_db))

    forbidden = list(row["expected"]["forbid_substrings"]) + [
        "CANARY_SECRET_",
        "parent_secret",
        "raw_headers",
        "Authorization",
        row["ticket"],
    ]
    blobs = [repr(result)] + [p.read_text(encoding="utf-8") for p in _run_files(result)]
    for blob in blobs:
        for needle in forbidden:
            assert needle not in blob, f"{claim_id}: leaked {needle!r}"


def test_handoff_in_trace_is_allowlisted_and_read_only(public_db):
    result = run_claim("C-010", seed=11, store_path=str(public_db))
    handoffs = [e["payload"] for e in _events(result) if e["kind"] == "handoff"]

    assert len(handoffs) == 1
    handoff = handoffs[0]
    assert set(handoff) <= ALLOWED_FIELDS
    assert set(handoff["facts"]) <= FACT_KEYS
    assert set(handoff["allowed_tools"]) <= READONLY_TOOLS
    assert not set(handoff["allowed_tools"]) & WRITE_TOOLS
    assert handoff["claim_id"] == "C-010"


@pytest.mark.parametrize("claim_id, seed", [("C-002", 55), ("C-006", 11), ("C-009", 11)])
def test_ticket_text_never_changes_the_quote(public_db, claim_id, seed):
    """Ticket delay numbers and 'ignore policy' are user data, not instructions."""
    result = run_claim(claim_id, seed=seed, store_path=str(public_db))

    assert result.status == "denied"
    assert result.proposal_amount == 0
    assert result.refund_effects == []


def test_rerun_same_claim_keeps_one_refund(public_db):
    first = run_claim("C-001", seed=11, store_path=str(public_db))
    second = run_claim("C-001", seed=22, store_path=str(public_db))

    assert first.status == second.status == "resolved"
    assert first.proposal_amount == second.proposal_amount == 35000
    conn = connect(public_db)
    try:
        assert int(get_refund(conn, "C-001")["amount_vnd"]) == 35000
        assert committed_audit_count(conn, "C-001") == 1
        count = conn.execute(
            "SELECT COUNT(*) FROM refund_ledger WHERE claim_id = ?", ("C-001",)
        ).fetchone()[0]
        assert count == 1
    finally:
        conn.close()


@pytest.mark.parametrize("point", ["before_effect", "after_effect_before_checkpoint", "after_checkpoint"])
def test_crash_propagates_then_resume_gives_one_refund(public_db, point):
    with pytest.raises(InjectedCrash):
        run_claim("C-001", seed=11, store_path=str(public_db), crash_at=point)

    resumed = run_claim("C-001", seed=11, store_path=str(public_db))
    assert resumed.status == "resolved"
    assert [int(e["amount_vnd"]) for e in resumed.refund_effects] == [35000]
    assert resumed.committed_audit_count == 1
