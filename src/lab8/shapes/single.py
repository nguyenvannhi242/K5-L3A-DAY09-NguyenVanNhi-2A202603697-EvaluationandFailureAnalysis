"""Shape B — one ReAct agent. Student work.

Use `lab8.runtime.run_react` with max_steps=8. `decide` may pick a read tool.
It must not call `issue_refund`. After the loop, the host validates the quote
and is the only writer.

A decide() that trusts ticket text over the order row will fail hidden claims
where the customer overstates the delay or cites a superseded clause.

Per-order cap: an order may already have been refunded by an earlier claim.
The read tool `lookup_refund_history` shows how much. The host still enforces
the cap, so pass the full quote to `durability.issue_refund`, not a net amount.

Caps: stop at 8 steps. Count model calls in the Budget so cost stays comparable.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.mock_llm import complete
from lab8.runtime import run_react
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish
from lab8.trace import Trace


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


def _run(
    conn: sqlite3.Connection,
    claim_id: str,
    *,
    seed: int,
    crash_at: str | None,
) -> RunResult:
    budget = Budget()
    trace = Trace(make_run_id("single", claim_id, seed))

    def decide(observation: dict[str, Any]) -> dict[str, Any]:
        """Choose the next read step from bounded, sanitized state."""
        last_tool = observation.get("last_tool")
        last_result = observation.get("last_result") or {}

        # MockLLM accounts for the model decision. Tool selection stays inside
        # an allowlisted state machine, so ticket text cannot choose a write.
        complete(
            budget,
            seed=seed,
            purpose="single_decide",
            prompt=_decision_prompt(last_tool, last_result, claim_id),
        )

        if last_tool is None:
            trace.add("agent_action", {"tool": "get_claim_view"})
            return {"tool": "get_claim_view", "args": {"claim_id": claim_id}}

        if last_tool == "get_claim_view":
            if not last_result.get("found"):
                return {
                    "final": {
                        "status": "failed",
                        "proposal_amount": 0,
                        "error_reason": "unknown_claim",
                    }
                }
            if not last_result.get("order_id"):
                return {
                    "final": {
                        "status": "needs_info",
                        "proposal_amount": 0,
                        "error_reason": "missing_order_id",
                    }
                }
            trace.add("agent_action", {"tool": "lookup_order"})
            return {
                "tool": "lookup_order",
                "args": {
                    "order_id": last_result["order_id"],
                    "principal_id": last_result["principal_id"],
                },
            }

        if last_tool == "lookup_order":
            if not last_result.get("visible"):
                return {
                    "final": {
                        "status": "escalated",
                        "proposal_amount": 0,
                        "error_reason": last_result.get("reason"),
                    }
                }
            trace.add("agent_action", {"tool": "compute_compensation"})
            return {
                "tool": "compute_compensation",
                "args": {"order": last_result["order"]},
            }

        if last_tool == "compute_compensation":
            return {
                "final": {
                    "status": "quote_ready",
                    "proposal_amount": int(last_result["amount_vnd"]),
                    "error_reason": last_result.get("reason"),
                }
            }

        return {
            "final": {
                "status": "failed",
                "proposal_amount": 0,
                "error_reason": "unexpected_agent_state",
            }
        }

    loop = run_react(
        conn,
        budget,
        observation={"claim": {"claim_id": claim_id}},
        decide=decide,
        max_steps=8,
    )
    final = loop["final"]
    trace.add("agent_stop", {"reason": loop["stopped"], "status": final["status"]})

    if final["status"] != "quote_ready":
        return finish(
            conn,
            shape="single",
            claim_id=claim_id,
            seed=seed,
            status=final["status"],
            proposal_amount=int(final.get("proposal_amount") or 0),
            budget=budget,
            trace=trace,
            error_reason=final.get("error_reason"),
        )

    proposal = int(final["proposal_amount"])
    complete(budget, seed=seed, purpose="draft", prompt=f"proposal={proposal}")
    trace.add("quote", {"amount_vnd": proposal, "reason": final.get("error_reason")})

    if proposal <= 0:
        return finish(
            conn,
            shape="single",
            claim_id=claim_id,
            seed=seed,
            status="denied",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason=final.get("error_reason"),
        )

    try:
        outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    except InjectedCrash:
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise

    if not outcome["ok"]:
        return finish(
            conn,
            shape="single",
            claim_id=claim_id,
            seed=seed,
            status="failed" if outcome["conflict"] else "denied",
            proposal_amount=proposal,
            budget=budget,
            trace=trace,
            error_reason=outcome["reason"],
        )

    return finish(
        conn,
        shape="single",
        claim_id=claim_id,
        seed=seed,
        status="resolved",
        proposal_amount=proposal,
        budget=budget,
        trace=trace,
        error_reason=None,
    )


def _decision_prompt(last_tool: str | None, last_result: dict[str, Any], claim_id: str) -> str:
    """Describe state without copying ticket text or sensitive rows."""
    if last_tool is None:
        return f"claim={claim_id}; choose the first read tool"
    if last_tool == "get_claim_view":
        return (
            f"claim_found={bool(last_result.get('found'))}; "
            f"has_order_id={bool(last_result.get('order_id'))}; choose the next read step"
        )
    if last_tool == "lookup_order":
        return (
            f"order_visible={bool(last_result.get('visible'))}; "
            f"reason={last_result.get('reason')}; choose the next read step"
        )
    if last_tool == "compute_compensation":
        return "deterministic quote is ready; finalize the read loop"
    return "unexpected state; stop safely"
