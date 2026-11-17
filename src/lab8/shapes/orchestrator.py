"""Shape C — orchestrator plus read-only investigators. Student work.

1. Build a handoff with `lab8.handoff.make_handoff`. Allowed tools are read-only.
   Never put parent_secret, raw headers, or the full claim row into facts.
2. `lab8.runtime.run_readonly_investigators` runs those tools.
3. The orchestrator quotes from the investigator's order view and calls
   `durability.issue_refund`. Investigators have no write registry.
   Pass the full quote; the host subtracts earlier refunds on the same order.
   `lookup_refund_history` is a read-only tool you may hand to an investigator.
4. Trace the sanitized handoff, not the parent context.

An LLM summary of the parent context is not a sanitizer. Use the allowlist.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import sqlite3

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.handoff import make_handoff
from lab8.mock_llm import complete
from lab8.runtime import run_readonly_investigators
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import get_claim_view, tool_cost
from lab8.trace import Trace

SHAPE = "orchestrator"
# Read-only tools handed to the investigator. No write tool ever crosses the boundary.
INVESTIGATOR_TOOLS = ["lookup_order", "lookup_policy", "compute_compensation"]
# Order fields that are safe to trace (no phone, address, item name or internal note).
TRACE_ORDER_FIELDS = ("order_id", "status", "delay_days", "shipping_fee_vnd", "carrier")


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    conn = connect(store_path)
    try:
        return _run(conn, claim_id, seed=seed, crash_at=crash_at)
    finally:
        conn.close()


def _run(conn: sqlite3.Connection, claim_id: str, *, seed: int, crash_at: str | None) -> RunResult:
    budget = Budget()
    trace = Trace(make_run_id(SHAPE, claim_id, seed))

    def done(status: str, proposal: int, reason: str | None) -> RunResult:
        trace.add("decision", {"status": status, "proposal_amount": int(proposal), "reason": reason})
        return finish(
            conn,
            shape=SHAPE,
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal,
            budget=budget,
            trace=trace,
            error_reason=reason,
        )

    # 1. Orchestrator reads the claim through the allowlisted view (no parent_secret).
    claim = get_claim_view(conn, claim_id)
    budget.add_tool(tool_cost())
    if not claim["found"]:
        return done("failed", 0, "unknown_claim")
    trace.add("claim", {"claim_id": claim_id, "has_order_id": bool(claim.get("order_id"))})
    if not claim.get("order_id"):
        return done("needs_info", 0, "missing_order_id")

    # 2. Typed handoff. facts stay empty: the ticket is user data, not a fact source.
    handoff = make_handoff(
        request_id=f"REQ-{claim_id}",
        claim_id=claim_id,
        task="quote_claim_from_order_row",
        facts={},
        allowed_tools=list(INVESTIGATOR_TOOLS),
    )
    trace.add("handoff", safe_dump(handoff))

    # 3. Read-only investigator.
    investigated = run_readonly_investigators(
        conn, budget, handoff, principal_id=claim["principal_id"]
    )
    tools = investigated["tools"]
    order_view = tools.get("lookup_order") or {}
    if not order_view.get("visible"):
        trace.add("investigator", {"order_visible": False, "reason": order_view.get("reason")})
        return done("escalated", 0, order_view.get("reason"))

    order = order_view["order"]
    trace.add(
        "investigator",
        {
            "order_visible": True,
            "order": {key: order.get(key) for key in TRACE_ORDER_FIELDS},
            "policy_count": len((tools.get("lookup_policy") or {}).get("policies") or []),
        },
    )

    # 4. Money is decided by code (compute_compensation on the order row), never by the model.
    quote_payload = tools.get("compute_compensation") or {"amount_vnd": 0, "reason": "no_quote"}
    proposal = int(quote_payload.get("amount_vnd") or 0)
    trace.add("quote", {"amount_vnd": proposal, "reason": quote_payload.get("reason")})

    # MockLLM only drafts the reply text; it sees the status, not the ticket or secrets.
    complete(budget, seed=seed, purpose="draft", prompt=f"proposal={proposal}")

    if proposal <= 0:
        return done("denied", 0, quote_payload.get("reason"))

    # 5. Orchestrator (not the investigator) calls the host with the FULL quote.
    try:
        outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    except InjectedCrash:
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise

    trace.add("host", {"ok": bool(outcome["ok"]), "reason": outcome.get("reason")})
    if not outcome["ok"]:
        status = "failed" if outcome.get("conflict") else "denied"
        return done(status, proposal, outcome.get("reason"))
    return done("resolved", proposal, None)
