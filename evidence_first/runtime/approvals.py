from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from evidence_first.models import Approval


@dataclass
class ApprovalRequest:
    approval_id: str = field(default_factory=lambda: "appr_" + uuid.uuid4().hex[:10])
    action_id: str = ""
    requested_permission: str = ""
    reason: str = ""
    status: str = "pending"
    requested_at: str = ""
    decided_at: Optional[str] = None
    decision: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "action_id": self.action_id,
            "requested_permission": self.requested_permission,
            "reason": self.reason,
            "status": self.status,
            "requested_at": self.requested_at,
            "decided_at": self.decided_at,
            "decision": self.decision,
        }

    def to_structured_response(self) -> dict[str, Any]:
        return {
            "approval_required": True,
            "action_id": self.action_id,
            "reason": self.reason,
            "requested_permission": self.requested_permission,
            "approval_id": self.approval_id,
        }


def create_approval_request(action_id: str, requested_permission: str, reason: str) -> ApprovalRequest:
    return ApprovalRequest(
        action_id=action_id,
        requested_permission=requested_permission,
        reason=reason,
        requested_at=datetime.now(timezone.utc).isoformat(),
    )


def approve(approval: ApprovalRequest) -> ApprovalRequest:
    approval.status = "approved"
    approval.decided_at = datetime.now(timezone.utc).isoformat()
    approval.decision = "approved"
    return approval


def deny(approval: ApprovalRequest) -> ApprovalRequest:
    approval.status = "denied"
    approval.decided_at = datetime.now(timezone.utc).isoformat()
    approval.decision = "denied"
    return approval


class ApprovalManager:
    def __init__(self):
        self.requests: dict[str, ApprovalRequest] = {}
        self.auto_approve: bool = False

    def request_approval(self, action_id: str, requested_permission: str, reason: str) -> ApprovalRequest:
        req = create_approval_request(action_id, requested_permission, reason)
        self.requests[req.approval_id] = req
        if self.auto_approve:
            approve(req)
        return req

    def get_pending(self) -> list[ApprovalRequest]:
        return [r for r in self.requests.values() if r.status == "pending"]

    def approve_by_id(self, approval_id: str) -> bool:
        req = self.requests.get(approval_id)
        if req and req.status == "pending":
            approve(req)
            return True
        return False

    def deny_by_id(self, approval_id: str) -> bool:
        req = self.requests.get(approval_id)
        if req and req.status == "pending":
            deny(req)
            return True
        return False

    def get_by_action(self, action_id: str) -> Optional[ApprovalRequest]:
        for req in self.requests.values():
            if req.action_id == action_id:
                return req
        return None
