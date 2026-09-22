from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:12]


@dataclass
class Task:
    task_id: str = field(default_factory=lambda: _uid("task_"))
    description: str = ""
    created_at: str = field(default_factory=_now_iso)


@dataclass
class Action:
    action_id: str = field(default_factory=lambda: _uid("act_"))
    type: str = ""
    tool: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    timestamp: str = field(default_factory=_now_iso)
    status: str = "pending"
    output: Optional[str] = None
    permission_required: str = "READ"


@dataclass
class Observation:
    observation_id: str = field(default_factory=lambda: _uid("obs_"))
    action_id: str = ""
    content: str = ""
    timestamp: str = field(default_factory=_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Evidence:
    evidence_id: str = field(default_factory=lambda: _uid("ev_"))
    observation_id: str = ""
    action_id: str = ""
    content: str = ""
    source_type: str = "document"
    filename: Optional[str] = None
    page: Optional[int] = None
    snippet: Optional[str] = None
    hash: Optional[str] = None
    url: Optional[str] = None
    timestamp: str = field(default_factory=_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Verification:
    verification_id: str = field(default_factory=lambda: _uid("ver_"))
    action_id: str = ""
    observation_id: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    verified: bool = False
    confidence: float = 0.0
    reason: str = ""
    status: str = "EVIDENCE_NOT_FOUND"
    timestamp: str = field(default_factory=_now_iso)


@dataclass
class Conclusion:
    conclusion_id: str = field(default_factory=lambda: _uid("con_"))
    requirement_id: Optional[str] = None
    verification_id: Optional[str] = None
    status: str = "NEEDS_HUMAN_REVIEW"
    reason: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=_now_iso)


@dataclass
class Approval:
    approval_id: str = field(default_factory=lambda: _uid("appr_"))
    action_id: str = ""
    requested_permission: str = ""
    reason: str = ""
    status: str = "pending"
    requested_at: str = field(default_factory=_now_iso)
    decided_at: Optional[str] = None
    decision: Optional[str] = None


@dataclass
class Run:
    run_id: str = field(default_factory=lambda: _uid("run_"))
    task_id: str = ""
    task_description: str = ""
    status: str = "running"
    started_at: str = field(default_factory=_now_iso)
    completed_at: Optional[str] = None
    steps: list[dict[str, Any]] = field(default_factory=list)
    results: dict[str, Any] = field(default_factory=dict)
    approval_requests: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GraphEdge:
    source_id: str = ""
    source_type: str = ""
    target_id: str = ""
    target_type: str = ""
    relationship: str = ""
    timestamp: str = field(default_factory=_now_iso)
