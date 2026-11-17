"""Extra checks for the bounded single-agent shape."""

from __future__ import annotations

import json

from lab8.shapes.single import run_claim


def test_single_uses_structured_order_instead_of_ticket_delay(public_db):
    result = run_claim("C-002", seed=55, store_path=str(public_db))

    assert result.status == "denied"
    assert result.proposal_amount == 0
    assert result.refund_effects == []
    assert result.model_calls > 0
    assert result.tool_calls == 3


def test_single_trace_contains_no_raw_ticket(public_db):
    result = run_claim("C-001", seed=11, store_path=str(public_db))
    trace = json.loads(open(result.trace_path, encoding="utf-8").read())
    serialized = json.dumps(trace, ensure_ascii=False)

    assert result.status == "resolved"
    assert result.proposal_amount == 35000
    assert "agent_action" in serialized
    assert "CANARY_SECRET" not in serialized
    assert "Giao trễ" not in serialized
