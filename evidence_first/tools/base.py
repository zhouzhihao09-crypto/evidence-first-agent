from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from evidence_first.models import Action


@dataclass
class ToolResult:
    success: bool
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class ToolCapability:
    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    permission_level: str = "READ"
    risk: str = "LOW"


class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]: ...

    @property
    def permission_required(self) -> str:
        return "READ"

    @property
    def risk_level(self) -> str:
        return "LOW"

    @abstractmethod
    def execute(self, action: Action) -> ToolResult: ...

    def can_verify(self) -> bool:
        return False

    def verify(self, action: Action, observation: str) -> dict[str, Any]:
        return {"verified": False, "confidence": 0.0, "reason": "No verification strategy defined"}
