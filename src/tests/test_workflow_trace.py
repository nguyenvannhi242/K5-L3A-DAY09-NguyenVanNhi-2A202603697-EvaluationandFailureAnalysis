from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab8.config_loader import repo_path
from lab8.db import load_jsonl
from lab8.durability import InjectedCrash
from lab8.shapes.workflow import run_claim

CLAIMS = load_jsonl(repo_path("data", "public_claims.jsonl"))
BY_ID = {row["claim_id"]: row for row in CLAIMS}
ALLOWED_KINDS = {"claim", "order", "quote", "host", "crash"}


def _events(result) -> list[dict]:
    body = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
    assert body["run_id"] == result.run_id
    return body["events"]


@pytest.mark.parametrize("claim_id", [row["claim_id"] for row in CLAIMS])
def test_workflow_trace_is_allowlisted_and_leaks_nothing(public_db, claim_id):
    row = BY_ID[claim_id]
    result = run_claim(claim_id, seed=11, store_path=str(public_db))
    events = _events(result)

    assert len(events) >= 1
    assert {e["kind"] for e in events} <= ALLOWED_KINDS

    blob = Path(result.trace_path).read_text(encoding="utf-8")
    for needle in ["CANARY_SECRET_", "parent_secret", "raw_headers", row["ticket"]]:
        assert needle not in blob, f"{claim_id}: leaked {needle!r}"


def test_workflow_ignores_ticket_delay_on_seed_55(public_db):
    # C-002: ticket says 5 days late, order row says 2. Swarm pays on seed 55; workflow must not.
    result = run_claim("C-002", seed=55, store_path=str(public_db))

    assert result.status == "denied"
    assert result.proposal_amount == 0
    assert result.refund_effects == []
    assert result.model_calls == 0


def test_workflow_crash_is_traced_then_reraised(public_db):
    with pytest.raises(InjectedCrash):
        run_claim("C-001", seed=11, store_path=str(public_db), crash_at="after_effect_before_checkpoint")

    trace = Path("traces") / "R-workflow-C-001-s11.json"
    kinds = [e["kind"] for e in json.loads(trace.read_text(encoding="utf-8"))["events"]]
    assert kinds[-1] == "crash"