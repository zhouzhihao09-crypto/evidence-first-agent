"""MCP v2 server exposing AI Work Receipts to MCP-compatible hosts.

Built on the official Python SDK v2 API (``mcp.server.mcpserver.MCPServer``,
the v2 rename of ``FastMCP``).

    AI agent  ->  MCP server (this module)  ->  Evidence-First core
                                              (WorkLedger, verifier, approvals)

The tools are intentionally thin. They translate MCP calls into core calls and
translate core results back. Two safety properties are structural:

* a claim is only VERIFIED when the core verification engine says so, and
* an action that needs ``SEND``/``SUBMIT``/``DELETE`` permission is recorded as
  blocked with a pending approval — there is no tool here that can approve one.
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Optional

from mcp.server.mcpserver import MCPServer

from evidence_first.evidence.store import EvidenceStore
from evidence_first.receipt import canonical_json
from evidence_first.verification import available_checks
from evidence_first.work import WorkLedger

SERVER_NAME = "evidence-first"
SERVER_INSTRUCTIONS = (
    "Evidence-first work tracking. Record what you do as evidence, state claims "
    "explicitly, let the verification engine decide their status, request approval "
    "for consequential actions, and finish the run to obtain a hashable AI Work "
    "Receipt. Never assert that work is verified: report the status returned here."
)

VERIFY_WORK_PROMPT = """\
Work in an evidence-first manner using the evidence-first MCP tools.

Before declaring the task complete:

1. Call work_start with the task, your agent name, and the objective.
2. Record what you actually did with evidence_record (files inspected, diffs,
   command output, scans). Record the raw output, not a summary of success.
3. Create explicit claims with claim_create. Attach a deterministic check
   (available: {checks}) whenever the claim can be checked mechanically, and
   link the evidence ids that support it.
4. Call verify for every claim. The verification engine is authoritative:
   report VERIFIED, FAILED, INSUFFICIENT_EVIDENCE, or NOT_VERIFIED exactly as
   returned. Never upgrade a status yourself.
5. Call approval_request before any consequential action (sending, submitting,
   deleting, modifying external systems). Do not attempt to work around a
   pending approval.
6. Call work_finish only after verification, then call receipt_get (or read the
   receipt:// resource) and present the receipt, including its SHA-256 hash and
   any NEEDS_REVIEW or INSUFFICIENT_EVIDENCE items.
"""


def _ok(data: Optional[dict[str, Any]] = None, **extra: Any) -> dict[str, Any]:
    """Uniform success envelope: hosts always get structured data, not prose."""
    return {"ok": True, **(data or {}), **extra}


def _error(message: str, data: Optional[dict[str, Any]] = None, **extra: Any) -> dict[str, Any]:
    """Uniform failure envelope: a machine-readable error field."""
    return {"ok": False, "error": message, **(data or {}), **extra}


def _missing_run(run_id: str) -> dict[str, Any]:
    return _error(f"Run not found: {run_id}")


def create_server(
    db_path: Optional[str] = None,
    store: Optional[EvidenceStore] = None,
    workdir: Optional[str] = None,
) -> MCPServer:
    """Build the MCP server. ``store`` lets tests inject an isolated database."""
    resolved_store = store if store is not None else EvidenceStore(db_path=db_path)
    ledger = WorkLedger(store=resolved_store, workdir=workdir)
    server = MCPServer(
        name=SERVER_NAME,
        version="0.2.0",
        instructions=SERVER_INSTRUCTIONS,
    )

    # --- Tools -------------------------------------------------------------

    @server.tool(
        name="work_start",
        title="Start a verifiable work run",
        description=(
            "Open a work run that will be summarized into an AI Work Receipt. "
            "Returns the run_id used by every other tool."
        ),
    )
    def work_start(task: str, agent: str, objective: Optional[str] = None) -> dict[str, Any]:
        if not task or not task.strip():
            return _error("task must not be empty")
        if not agent or not agent.strip():
            return _error("agent must not be empty")
        run = ledger.start_run(task=task, agent=agent, objective=objective)
        return _ok(
            {
                "run_id": run.run_id,
                "status": run.status,
                "task": run.task_description,
                "agent": run.results.get("agent", ""),
                "objective": run.results.get("objective", ""),
                "started_at": run.started_at,
            }
        )

    @server.tool(
        name="evidence_record",
        title="Record evidence",
        description=(
            "Record something observed or produced during the run (command output, "
            "diff, scan result, file contents). Credential-shaped values are "
            "redacted before they are stored. Records an action, an observation, "
            "and one evidence item linked in the evidence graph."
        ),
    )
    def evidence_record(
        run_id: str,
        type: str,
        description: str,
        source: Optional[str] = None,
        content: Optional[str] = None,
    ) -> dict[str, Any]:
        if not run_id:
            return _error("run_id must not be empty")
        if not description or not description.strip():
            return _error("description must not be empty")
        if not type or not type.strip():
            return _error("type must not be empty")
        if ledger.get_run(run_id) is None:
            return _missing_run(run_id)
        recorded = ledger.record_evidence(
            run_id=run_id,
            evidence_type=type,
            description=description,
            source=source,
            content=content,
        )
        if "error" in recorded:
            return _error(recorded["error"])
        return _ok(
            {
                "run_id": run_id,
                "action_id": recorded.get("action_id", ""),
                "observation_id": recorded.get("observation_id", ""),
                "evidence_id": recorded.get("evidence_id", ""),
                "permission_required": recorded.get("permission_required", ""),
                "type": type,
                "description": description,
            }
        )

    @server.tool(
        name="claim_create",
        title="Create a verifiable claim",
        description=(
            "Create a statement that must be verified, e.g. 'All authentication "
            "tests pass'. Optionally attach a deterministic check: "
            f"{', '.join(available_checks())} (e.g. "
            '{"type": "keyword_present", "keyword": "passed"}). '
            "A claim without a check stays NOT_VERIFIED: it is never auto-verified."
        ),
    )
    def claim_create(
        run_id: str,
        statement: str,
        check: Optional[dict[str, Any]] = None,
        evidence_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        if not run_id:
            return _error("run_id must not be empty")
        if not statement or not statement.strip():
            return _error("statement must not be empty")
        if ledger.get_run(run_id) is None:
            return _missing_run(run_id)
        created = ledger.create_claim(
            run_id=run_id, statement=statement, check=check, evidence_ids=evidence_ids
        )
        if "error" in created:
            return _error(created["error"])
        return _ok(**created)

    @server.tool(
        name="verify",
        title="Verify a claim",
        description=(
            "Run the deterministic verification engine over a claim and persist the "
            "result. Returns the engine's status (VERIFIED, FAILED, "
            "INSUFFICIENT_EVIDENCE, NOT_VERIFIED) with its reason and the evidence it "
            "used. The engine decides; this tool only reports."
        ),
    )
    def verify(claim_id: str) -> dict[str, Any]:
        if not claim_id or not claim_id.strip():
            return _error("claim_id must not be empty")
        outcome = ledger.verify_claim(claim_id)
        if "error" in outcome:
            return _error(outcome["error"])
        return _ok(
            {
                "claim_id": outcome["claim_id"],
                "run_id": outcome["run_id"],
                "statement": outcome["statement"],
                "status": outcome["status"],
                "reason": outcome["reason"],
                "verification_id": outcome["verification_id"],
                "evidence": outcome["evidence_ids"],
                "evidence_considered": outcome["evidence_considered"],
            }
        )

    @server.tool(
        name="approval_request",
        title="Request approval for consequential work",
        description=(
            "Route a consequential action through the existing approval gate. "
            "Actions requiring SEND/SUBMIT/DELETE permission are recorded as blocked "
            "with a pending approval; the MCP surface provides no way to approve, so "
            "a human must decide outside this server."
        ),
    )
    def approval_request(
        run_id: str,
        action: str,
        reason: str,
        permission: Optional[str] = None,
    ) -> dict[str, Any]:
        if not run_id:
            return _error("run_id must not be empty")
        if not action or not action.strip():
            return _error("action must not be empty")
        if not reason or not reason.strip():
            return _error("reason must not be empty")
        if ledger.get_run(run_id) is None:
            return _missing_run(run_id)
        outcome = ledger.request_approval(
            run_id=run_id, action=action, reason=reason, permission=permission
        )
        if "error" in outcome:
            return _error(outcome["error"])
        return _ok(**outcome)

    @server.tool(
        name="work_finish",
        title="Finish a run and finalize its receipt",
        description=(
            "Close the run and produce the AI Work Receipt. Returns the run status, "
            "the receipt result (VERIFIED, FAILED, NEEDS_REVIEW) and the receipt "
            "SHA-256 hash. Pending approvals force NEEDS_REVIEW."
        ),
    )
    def work_finish(run_id: str) -> dict[str, Any]:
        if not run_id or not run_id.strip():
            return _error("run_id must not be empty")
        if ledger.get_run(run_id) is None:
            return _missing_run(run_id)
        outcome = ledger.finish_run(run_id)
        if "error" in outcome:
            return _error(outcome["error"])
        return _ok(
            {
                "run_id": outcome["run_id"],
                "status": outcome["status"],
                "result": outcome["result"],
                "receipt_hash": outcome["receipt_hash"],
                "counts": outcome["receipt"]["result"] if outcome.get("receipt") else {},
            }
        )

    @server.tool(
        name="receipt_get",
        title="Retrieve the AI Work Receipt",
        description=(
            "Return the complete, versioned AI Work Receipt for a run: run metadata, "
            "actions, evidence references, claims, verifications, approvals, result, "
            "and the SHA-256 integrity hash."
        ),
    )
    def receipt_get(run_id: str) -> dict[str, Any]:
        if not run_id or not run_id.strip():
            return _error("run_id must not be empty")
        built = ledger.get_receipt(run_id)
        if built is None:
            return _missing_run(run_id)
        return _ok({"receipt": built})

    # --- Resources ---------------------------------------------------------

    @server.resource(
        "receipt://run/{run_id}",
        name="work_receipt",
        title="AI Work Receipt",
        description="The complete hashable work receipt for a run.",
        mime_type="application/json",
    )
    def receipt_resource(run_id: str) -> str:
        built = ledger.get_receipt(run_id)
        if built is None:
            raise ValueError(f"Run not found: {run_id}")
        return canonical_json(built)

    @server.resource(
        "evidence://run/{run_id}",
        name="run_evidence",
        title="Evidence records for a run",
        description="Evidence items recorded during a run, with hashes and snippets.",
        mime_type="application/json",
    )
    def evidence_resource(run_id: str) -> str:
        if ledger.get_run(run_id) is None:
            raise ValueError(f"Run not found: {run_id}")
        items = ledger.store.get_evidence(run_id)
        return canonical_json({"run_id": run_id, "evidence": items})

    @server.resource(
        "audit://run/{run_id}",
        name="run_audit_trail",
        title="Audit trail for a run",
        description="Chronological trail of actions, observations, evidence, claims, verifications, approvals.",
        mime_type="application/json",
    )
    def audit_resource(run_id: str) -> str:
        trail = ledger.get_audit_trail(run_id)
        if trail is None:
            raise ValueError(f"Run not found: {run_id}")
        return canonical_json(trail)

    # --- Prompt ------------------------------------------------------------

    @server.prompt(
        name="verify_work",
        title="Verify work before claiming completion",
        description="Instruct an agent to work in an evidence-first manner and finish with a Work Receipt.",
    )
    def verify_work() -> str:
        return VERIFY_WORK_PROMPT.format(checks=", ".join(available_checks()))

    return server


def main(
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8765,
    db_path: Optional[str] = None,
) -> None:
    """Entry point for ``python -m evidence_first.mcp.server`` and the CLI."""
    server = create_server(db_path=db_path)
    if transport == "streamable-http":
        server.run(transport="streamable-http", host=host, port=port)
    else:
        server.run(transport="stdio")


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Evidence-First Agent MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host")
    parser.add_argument("--port", default=8765, type=int, help="HTTP bind port")
    parser.add_argument(
        "--db",
        dest="db_path",
        default=os.path.join(os.getcwd(), ".evidence_first", "evidence.db"),
        help="Evidence database path",
    )
    args = parser.parse_args()
    main(transport=args.transport, host=args.host, port=args.port, db_path=args.db_path)


if __name__ == "__main__":
    _cli()
