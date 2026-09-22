import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from evidence_first.models import (
    Action, Observation, Evidence, Verification, Conclusion,
    Approval, Run, Task, GraphEdge,
)
from evidence_first.evidence.store import EvidenceStore
from evidence_first.evidence.recorder import record_action, record_observation, record_evidence, record_verification, record_conclusion, add_graph_edge
from evidence_first.agent.planner import Planner
from evidence_first.agent.executor import Executor
from evidence_first.agent.verifier import Verifier
from evidence_first.agent.tender_verifier import TenderVerifier
from evidence_first.runtime.permissions import (
    get_required_permission, get_risk_level, requires_approval, can_execute_with,
)
from evidence_first.runtime.approvals import ApprovalManager


class AgentRuntime:
    def __init__(
        self,
        llm=None,
        tools: Optional[list] = None,
        auto_approve: bool = False,
        store: Optional[EvidenceStore] = None,
    ):
        self._llm = llm
        self._store = store or EvidenceStore()
        self._planner = Planner(llm=llm)
        self._executor = Executor()
        self._verifier = Verifier()
        self._verifier.register_verifier("document_search", TenderVerifier.verify)
        self._approvals = ApprovalManager()
        self._approvals.auto_approve = auto_approve
        self._run: Optional[Run] = None
        self._task: Optional[Task] = None

        if tools:
            for tool in tools:
                self._executor.register_tool(tool)

    def run_task(self, task_description: str, task_id: Optional[str] = None) -> Run:
        task = Task(
            task_id=task_id or "task_" + uuid.uuid4().hex[:8],
            description=task_description,
        )
        self._task = task
        run = Run(
            run_id="run_" + uuid.uuid4().hex[:10],
            task_id=task.task_id,
            task_description=task_description,
            status="running",
        )
        self._run = run
        self._store.save_run(run)

        try:
            plan = self._planner.plan(task)
            for step in plan:
                self._execute_step(run, step, task)
                if self._has_pending_approvals():
                    break

            run.status = "completed" if not self._has_pending_approvals() else "paused"
        except Exception as e:
            run.status = "failed"
            run.results["error"] = str(e)
        finally:
            run.completed_at = datetime.now(timezone.utc).isoformat()
            self._store.save_run(run)

        return run

    def _execute_step(self, run: Run, step: dict[str, Any], task: Task) -> None:
        action = Action(
            action_id="act_" + uuid.uuid4().hex[:10],
            type=step.get("action_type", ""),
            tool=step.get("tool", ""),
            input=step.get("input", {}),
            reason=step.get("reason", ""),
            permission_required=step.get("permission_required", "READ"),
        )

        required_permission = get_required_permission(action.type, action.tool)
        action.permission_required = required_permission
        risk = get_risk_level(action.type, action.tool)

        record_action(self._store, run.run_id, action)
        run.steps.append({
            "step": len(run.steps) + 1,
            "action_id": action.action_id,
            "type": action.type,
            "tool": action.tool,
            "status": "pending",
            "permission": required_permission,
            "risk": risk,
        })
        self._store.save_run(run)

        if requires_approval(required_permission):
            req = self._approvals.request_approval(
                action.action_id, required_permission,
                f"Action {action.type} requires {required_permission} due to {risk} risk.",
            )
            run.approval_requests.append(req.to_dict())
            self._store.save_run(run)
            if req.status == "pending":
                run.steps[-1]["status"] = "approval_pending"
                return

        if not can_execute_with("READ", required_permission):
            run.steps[-1]["status"] = "denied"
            return

        observation = self._executor.execute(action)
        if observation:
            record_observation(self._store, observation)

        evidence = Evidence(
            action_id=action.action_id,
            observation_id=observation.observation_id if observation else "",
            content=observation.content if observation else "",
            source_type="document",
            snippet=(observation.content or "")[:500] if observation else "",
            metadata={
                "tool": action.tool,
                "step": len(run.steps),
            },
        )
        record_evidence(self._store, evidence)
        add_graph_edge(self._store, action.action_id, "action", observation.observation_id if observation else "", "observation", "produces")
        add_graph_edge(self._store, observation.observation_id if observation else "", "observation", evidence.evidence_id, "evidence", "supports")

        verification = self._verifier.verify(action, observation, evidence=evidence)
        record_verification(self._store, verification)
        add_graph_edge(self._store, observation.observation_id if observation else "", "observation", verification.verification_id, "verification", "verified_by")

        run.steps[-1]["status"] = "completed" if verification.verified else "failed"
        run.results[action.action_id] = {
            "verification": verification.verified,
            "confidence": verification.confidence,
            "reason": verification.reason,
        }
        self._store.save_run(run)

    def _has_pending_approvals(self) -> bool:
        return len(self._approvals.get_pending()) > 0

    def approve_action(self, approval_id: str) -> bool:
        return self._approvals.approve_by_id(approval_id)

    def deny_action(self, approval_id: str) -> bool:
        return self._approvals.deny_by_id(approval_id)

    def resume_run(self, run_id: str) -> Run:
        run = self._store.get_run(run_id)
        if not run:
            raise ValueError(f"Run not found: {run_id}")
        self._run = Run(**run) if isinstance(run, dict) else run
        if self._run.status != "paused":
            raise ValueError(f"Run {run_id} is not paused")
        self._run.status = "running"
        self._store.save_run(self._run)
        pending = self._approvals.get_pending()
        for req in pending:
            action = self._store.get_actions(self._run.run_id)
            step_to_run = None
            for step in self._run.steps:
                if step.get("action_id") == req.action_id and step.get("status") == "approval_pending":
                    step_to_run = step
                    break
            if step_to_run and self._approvals.get_by_action(req.action_id):
                self._approvals.approve_by_id(req.approval_id)
            if step_to_run:
                self._execute_step(self._run, {
                    "action_type": step_to_run["type"],
                    "tool": step_to_run["tool"],
                    "input": {},
                    "reason": "Resuming after approval",
                    "permission_required": step_to_run.get("permission", "READ"),
                }, self._task)
        self._run.status = "completed"
        self._store.save_run(self._run)
        return self._run

    @property
    def store(self) -> EvidenceStore:
        return self._store
