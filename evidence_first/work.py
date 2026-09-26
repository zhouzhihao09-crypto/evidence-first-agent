"""WorkRun lifecycle — the engine behind receipts, CLI, and MCP.

The conceptual chain implemented here:

    WorkRun -> Actions -> Observations -> Evidence -> Claims -> Verification
            -> Approvals -> Work Receipt

Every write goes through the existing recorders
(:mod:`evidence_first.evidence.recorder`) and the existing permission and
approval modules. Distribution adapters (CLI, MCP server) call this class and
never re-implement verification or approval logic.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from evidence_first.evidence.recorder import (
    add_graph_edge,
    record_action,
    record_evidence,
    record_observation,
    record_verification,
)
from evidence_first.evidence.store import EvidenceStore
from evidence_first.models import (
    Action, Approval, Claim, Evidence, Observation, Run, Task, Verification,
)
from evidence_first.receipt import build_receipt, render_receipt_text
from evidence_first.redaction import redact
from evidence_first.runtime.approvals import ApprovalManager
from evidence_first.runtime.permissions import (
    PERMISSION_HIERARCHY,
    get_required_permission,
    get_risk_level,
    permission_level,
    requires_approval,
    resolve_agent_permission,
)
from evidence_first.verification import (
    NOT_VERIFIED,
    CheckContext,
    available_checks,
    verify_claim,
)

DEFAULT_AGENT_TOOL = "agent"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:10]


class WorkLedger:
    """Record-and-verify bookkeeping for AI work runs.

    ``auto_approve`` exists for local scripting and tests only. Agent-facing
    adapters (MCP server, receipt CLI) must leave it ``False`` so a pending
    approval stays pending until a human decides it.
    """

    def __init__(
        self,
        store: Optional[EvidenceStore] = None,
        workdir: Optional[str] = None,
        auto_approve: bool = False,
    ) -> None:
        self._store = store or EvidenceStore()
        self._workdir = workdir or os.getcwd()
        self._approvals = ApprovalManager()
        # Approval auto-approval is never enabled from an agent-facing adapter.
        self._approvals.auto_approve = bool(auto_approve)

    @property
    def store(self) -> EvidenceStore:
        return self._store

    # --- WorkRun ------------------------------------------------------------

    def start_run(self, task: str, agent: str, objective: Optional[str] = None) -> Run:
        """Open a verifiable work run."""
        task_record = Task(
            task_id=_uid("task_"),
            description=redact(task),
        )
        run = Run(
            run_id=_uid("run_"),
            task_id=task_record.task_id,
            task_description=redact(task),
            status="running",
        )
        run.results["agent"] = redact(agent or "unspecified")
        if objective:
            run.results["objective"] = redact(objective)
        self._store.save_run(run)
        return run

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        return self._store.get_run(run_id)

    # --- Actions / Observations / Evidence ----------------------------------

    def record_action(
        self,
        run_id: str,
        action_type: str,
        description: str,
        tool: str = DEFAULT_AGENT_TOOL,
        action_input: Optional[dict[str, Any]] = None,
        status: str = "success",
        observation: Optional[str] = None,
        observation_metadata: Optional[dict[str, Any]] = None,
        evidence: Optional[dict[str, Any]] = None,
        allow_finished: bool = False,
    ) -> dict[str, Any]:
        """Record one action and, optionally, its observation and evidence."""
        run = self._store.get_run(run_id)
        if not run:
            return {"error": f"Run not found: {run_id}"}
        if not allow_finished and run.get("status") not in ("running",):
            return {"error": f"Run {run_id} is {run.get('status')}; start a new run to record more work."}

        permission = get_required_permission(action_type, tool)
        action = Action(
            action_id=_uid("act_"),
            type=action_type,
            tool=tool,
            input=action_input or {},
            reason=description,
            status=status,
            permission_required=permission,
        )
        record_action(self._store, run_id, action)

        obs = Observation(
            observation_id=_uid("obs_"),
            action_id=action.action_id,
            content=redact(observation if observation is not None else description),
            metadata=observation_metadata or {"success": status == "success"},
        )
        record_observation(self._store, obs)
        add_graph_edge(self._store, action.action_id, "action", obs.observation_id, "observation", "produces")

        result: dict[str, Any] = {
            "action_id": action.action_id,
            "observation_id": obs.observation_id,
            "permission_required": permission,
            "risk": get_risk_level(action_type, tool),
        }

        if evidence:
            item = Evidence(
                evidence_id=_uid("ev_"),
                action_id=action.action_id,
                observation_id=obs.observation_id,
                content=redact(evidence.get("content", "")),
                source_type=evidence.get("type", "log"),
                filename=evidence.get("source"),
                snippet=redact(evidence.get("snippet") or evidence.get("content", ""))[:200] or None,
                hash=evidence.get("hash"),
                url=evidence.get("url"),
                metadata=evidence.get("metadata", {}),
            )
            record_evidence(self._store, item)
            add_graph_edge(
                self._store, obs.observation_id, "observation", item.evidence_id, "evidence", "supports"
            )
            result["evidence_id"] = item.evidence_id
        return result

    def record_evidence(
        self,
        run_id: str,
        evidence_type: str,
        description: str,
        source: Optional[str] = None,
        content: Optional[str] = None,
        status: str = "success",
        url: Optional[str] = None,
    ) -> dict[str, Any]:
        """Convenience wrapper: record an action and one evidence item.

        Recording byte-identical evidence twice is a no-op: the existing
        evidence id is returned with ``duplicate=True`` so a receipt never
        counts the same observation twice.
        """
        run = self._store.get_run(run_id)
        if not run:
            return {"error": f"Run not found: {run_id}"}
        if run.get("status") != "running":
            return {"error": f"Run {run_id} is {run.get('status')}; start a new run to record more work."}

        body = redact(content or description)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        for existing in self._store.get_evidence(run_id):
            if existing.get("hash") == f"{digest}" and (existing.get("filename") or None) == (source or None):
                return {
                    "action_id": existing.get("action_id", ""),
                    "observation_id": existing.get("observation_id", ""),
                    "evidence_id": existing.get("evidence_id", ""),
                    "permission_required": get_required_permission(evidence_type, DEFAULT_AGENT_TOOL),
                    "duplicate": True,
                }

        return self.record_action(
            run_id,
            action_type=evidence_type,
            description=description,
            observation=content or description,
            observation_metadata={"success": status == "success"},
            evidence={
                "type": evidence_type,
                "source": source,
                "content": body,
                "hash": digest,
                "url": url,
            },
        )

    # --- Claims -------------------------------------------------------------

    def create_claim(
        self,
        run_id: str,
        statement: str,
        check: Optional[dict[str, Any]] = None,
        evidence_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Register a verifiable statement about the work."""
        if not self._store.get_run(run_id):
            return {"error": f"Run not found: {run_id}"}
        run_status = (self._store.get_run(run_id) or {}).get("status")
        if run_status != "running":
            return {"error": f"Run {run_id} is {run_status}; start a new run to add claims."}
        if not statement or not statement.strip():
            return {"error": "Claim statement must not be empty."}
        if check is not None and not isinstance(check, dict):
            return {"error": "Claim check must be an object."}
        if isinstance(check, dict) and check.get("type") and check["type"] not in available_checks():
            return {
                "error": (
                    f"Unknown check type '{check['type']}'. "
                    f"Available: {', '.join(available_checks())}."
                )
            }
        claim = Claim(
            claim_id=_uid("claim_"),
            run_id=run_id,
            statement=statement,
            status=NOT_VERIFIED,
            evidence_ids=list(evidence_ids or []),
            check=check or {},
        )
        self._store.save_claim(claim)
        return {
            "claim_id": claim.claim_id,
            "run_id": run_id,
            "statement": claim.statement,
            "status": claim.status,
            "check": claim.check,
        }

    def verify_claim(self, claim_id: str) -> dict[str, Any]:
        """Verify a claim with the deterministic engine and persist the result."""
        claim = self._store.get_claim(claim_id)
        if not claim:
            return {"error": f"Claim not found: {claim_id}"}
        run = self._store.get_run(claim.get("run_id", ""))
        if not run:
            return {"error": f"Run not found: {claim.get('run_id', '')}"}

        run_evidence = self._store.get_evidence(claim["run_id"])
        linked = [e for e in run_evidence if e.get("evidence_id") in (claim.get("evidence_ids") or [])]
        considered = linked or run_evidence

        ctx = CheckContext(
            run_id=claim["run_id"],
            claim=claim,
            evidence=considered,
            verifications=self._store.get_verifications(claim["run_id"]),
            workdir=self._workdir,
        )
        outcome = verify_claim(claim, ctx)

        verification = Verification(
            action_id="",
            observation_id="",
            evidence_ids=outcome.evidence_ids,
            verified=outcome.status == "VERIFIED",
            confidence=1.0 if outcome.status in ("VERIFIED", "FAILED") else 0.0,
            reason=outcome.reason,
            status=outcome.status,
        )
        record_verification(self._store, verification)
        add_graph_edge(
            self._store, claim["claim_id"], "claim", verification.verification_id, "verification", "verified_by"
        )
        for evidence_id in outcome.evidence_ids:
            add_graph_edge(
                self._store, evidence_id, "evidence", verification.verification_id, "verification", "supports"
            )

        claim["status"] = outcome.status
        claim["verification_id"] = verification.verification_id
        claim["verified_at"] = _now()
        self._store.save_claim(Claim(
            claim_id=claim["claim_id"],
            run_id=claim["run_id"],
            statement=claim["statement"],
            status=claim["status"],
            evidence_ids=claim.get("evidence_ids", []),
            verification_id=claim["verification_id"],
            check=claim.get("check", {}),
            created_at=claim.get("created_at", _now()),
            verified_at=claim["verified_at"],
        ))

        return {
            "claim_id": claim["claim_id"],
            "run_id": claim["run_id"],
            "statement": claim["statement"],
            "status": outcome.status,
            "verification_id": verification.verification_id,
            "reason": outcome.reason,
            "evidence_ids": outcome.evidence_ids,
            "evidence_considered": len(considered),
            "evidence_linked": bool(linked),
        }

    # --- Approvals ----------------------------------------------------------

    def request_approval(
        self,
        run_id: str,
        action: str,
        reason: str,
        permission: Optional[str] = None,
    ) -> dict[str, Any]:
        """Route a consequential action through the existing approval gate.

        The gate is never skipped. An action whose name resolves to ``SEND`` or
        above is recorded as blocked with a pending approval, and an explicitly
        supplied ``permission`` can raise that bar but never lower it. A caller
        cannot clear a consequential action by declaring a lower permission.
        """
        run = self._store.get_run(run_id)
        if not run:
            return {"error": f"Run not found: {run_id}"}
        if run.get("status") != "running":
            return {"error": f"Run {run_id} is {run.get('status')}; approvals cannot be requested now."}

        if permission and permission not in PERMISSION_HIERARCHY:
            return {
                "error": (
                    f"Unknown permission '{permission}'. Expected one of: "
                    f"{', '.join(sorted(PERMISSION_HIERARCHY))}."
                )
            }

        resolved, source = resolve_agent_permission(action, DEFAULT_AGENT_TOOL)
        if permission and source == "unknown":
            # The name rules could not classify this action and the caller did.
            required, source = permission, "explicit"
        elif permission and permission_level(permission) > permission_level(resolved):
            # An explicit permission may raise the bar, never lower it.
            required, source = permission, "explicit"
        else:
            required = resolved
        risk = get_risk_level(action, DEFAULT_AGENT_TOOL)

        # Fail closed: an action we cannot classify is treated as consequential.
        unknown_action = source == "unknown"
        if not unknown_action and not requires_approval(required):
            recorded = self.record_action(
                run_id,
                action_type=action,
                description=reason,
                status="success",
            )
            return {
                "approval_required": False,
                "status": "recorded",
                "action_id": recorded.get("action_id", ""),
                "requested_permission": required,
                "risk": risk,
                "reason": reason,
            }

        if unknown_action:
            gate_reason = (
                f"Action '{action}' is not in the permission map, so it is treated as "
                f"consequential (fail closed): {reason}"
            )
        else:
            gate_reason = (
                f"Action '{action}' requires {required} permission ({risk} risk): {reason}"
            )

        recorded = self.record_action(
            run_id,
            action_type=action,
            description=reason,
            status="approval_pending",
        )
        action_id = recorded.get("action_id", "")

        request = self._approvals.request_approval(action_id, required, gate_reason)
        approval = Approval(
            approval_id=request.approval_id,
            action_id=action_id,
            run_id=run_id,
            requested_permission=required,
            reason=redact(request.reason),
            status=request.status,
            requested_at=request.requested_at,
        )
        self._store.save_approval(approval)

        run_record = Run(
            run_id=run.get("run_id", run_id),
            task_id=run.get("task_id", ""),
            task_description=run.get("task_description", ""),
            status=run.get("status", "running"),
            started_at=run.get("started_at", ""),
            completed_at=run.get("completed_at"),
            steps=run.get("steps", []) if isinstance(run.get("steps"), list) else [],
            results=run.get("results", {}) if isinstance(run.get("results"), dict) else {},
            approval_requests=list(run.get("approval_requests") or []) + [
                {
                    "approval_id": approval.approval_id,
                    "action_id": action_id,
                    "requested_permission": required,
                    "reason": approval.reason,
                    "status": approval.status,
                    "requested_at": approval.requested_at,
                    "decided_at": None,
                    "decision": None,
                }
            ],
        )
        self._store.save_run(run_record)

        return {
            "approval_required": True,
            "status": request.status,
            "approval_id": approval.approval_id,
            "action_id": action_id,
            "requested_permission": required,
            "risk": risk,
            "reason": approval.reason,
            "blocked": request.status == "pending",
        }

    def pending_approvals(self, run_id: str) -> list[dict[str, Any]]:
        return self._store.get_pending_approvals(run_id)

    # --- Completion ---------------------------------------------------------

    def finish_run(self, run_id: str) -> dict[str, Any]:
        """Close a run and finalize its receipt."""
        run = self._store.get_run(run_id)
        if not run:
            return {"error": f"Run not found: {run_id}"}

        record = Run(
            run_id=run.get("run_id", run_id),
            task_id=run.get("task_id", ""),
            task_description=run.get("task_description", ""),
            status=run.get("status", "running"),
            started_at=run.get("started_at", ""),
            completed_at=run.get("completed_at"),
            steps=run.get("steps", []) if isinstance(run.get("steps"), list) else [],
            results=run.get("results", {}) if isinstance(run.get("results"), dict) else {},
            approval_requests=run.get("approval_requests", []) if isinstance(run.get("approval_requests"), list) else [],
        )
        already_finished = bool(record.results.get("finished_at"))
        if record.status == "running":
            record.status = "completed"
        if not record.results.get("finished_at"):
            record.results["finished_at"] = record.completed_at or _now()
        record.completed_at = record.results["finished_at"]
        self._store.save_run(record)

        if not already_finished:
            self.record_action(
                run_id,
                action_type="finish_run",
                description="Work run finished; receipt finalized.",
                status="success",
                allow_finished=True,
            )

        receipt = build_receipt(self._store, run_id)
        return {
            "run_id": run_id,
            "status": record.status,
            "result": receipt["result"]["status"] if receipt else NOT_VERIFIED,
            "receipt_hash": receipt["integrity"]["hash"] if receipt else "",
            "receipt": receipt,
        }

    def get_receipt(self, run_id: str) -> Optional[dict[str, Any]]:
        return build_receipt(self._store, run_id)

    def receipt_text(self, run_id: str) -> Optional[str]:
        receipt = self.get_receipt(run_id)
        return render_receipt_text(receipt) if receipt else None

    # --- Audit trail --------------------------------------------------------

    def get_audit_trail(self, run_id: str) -> Optional[dict[str, Any]]:
        """Chronological, flattened view of everything recorded for a run."""
        run = self._store.get_run(run_id)
        if not run:
            return None

        events: list[dict[str, Any]] = []
        events.append({
            "timestamp": run.get("started_at", ""),
            "event": "run_started",
            "id": run.get("run_id", ""),
            "detail": redact(run.get("task_description", "")),
        })

        observations = {o["observation_id"]: o for o in self._store.get_observations(run_id)}
        evidence_by_action: dict[str, list[dict[str, Any]]] = {}
        for item in self._store.get_evidence(run_id):
            evidence_by_action.setdefault(item.get("action_id", ""), []).append(item)
        verifications_by_action: dict[str, list[dict[str, Any]]] = {}
        for verification in self._store.get_verifications(run_id):
            verifications_by_action.setdefault(verification.get("action_id", ""), []).append(verification)

        for action in self._store.get_actions(run_id):
            events.append({
                "timestamp": action.get("timestamp", ""),
                "event": "action",
                "id": action.get("action_id", ""),
                "detail": f"{action.get('type', '')} via {action.get('tool', '')} [{action.get('status', '')}]",
            })
            observation = next(
                (o for o in observations.values() if o.get("action_id") == action.get("action_id")), None
            )
            if observation:
                events.append({
                    "timestamp": observation.get("timestamp", ""),
                    "event": "observation",
                    "id": observation.get("observation_id", ""),
                    "detail": redact(observation.get("content", ""))[:160],
                })
            for item in evidence_by_action.get(action.get("action_id", ""), []):
                events.append({
                    "timestamp": item.get("timestamp", ""),
                    "event": "evidence",
                    "id": item.get("evidence_id", ""),
                    "detail": f"{item.get('source_type', '')} {item.get('filename') or ''}".strip(),
                })
            for verification in verifications_by_action.get(action.get("action_id", ""), []):
                events.append({
                    "timestamp": verification.get("timestamp", ""),
                    "event": "verification",
                    "id": verification.get("verification_id", ""),
                    "detail": f"{verification.get('status', '')}: {verification.get('reason', '')}",
                })

        for claim in self._store.get_claims(run_id):
            events.append({
                "timestamp": claim.get("created_at", ""),
                "event": "claim",
                "id": claim.get("claim_id", ""),
                "detail": f"{claim.get('status', '')}: {redact(claim.get('statement', ''))}",
            })
            if claim.get("verification_id"):
                events.append({
                    "timestamp": claim.get("verified_at") or claim.get("created_at", ""),
                    "event": "claim_verification",
                    "id": claim["verification_id"],
                    "detail": f"{claim.get('status', '')} for {claim.get('claim_id', '')}",
                })
        for approval in self._store.get_approvals(run_id):
            events.append({
                "timestamp": approval.get("requested_at", ""),
                "event": "approval",
                "id": approval.get("approval_id", ""),
                "detail": f"{approval.get('status', '')} {approval.get('requested_permission', '')}",
            })

        events.sort(key=lambda e: (str(e.get("timestamp") or ""), str(e.get("event")), str(e.get("id"))))
        return {
            "run_id": run_id,
            "task": redact(run.get("task_description", "")),
            "status": run.get("status", ""),
            "events": events,
        }

    def close(self) -> None:
        self._store.close()
