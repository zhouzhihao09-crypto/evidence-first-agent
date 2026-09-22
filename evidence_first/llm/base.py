from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class LLMMessage:
    role: str
    content: str


@dataclass
class LLMResponse:
    content: str
    tool_calls: Optional[List[dict[str, Any]]] = None
    model: str = ""


class LLMProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def generate(self, messages: List[LLMMessage], **kwargs) -> LLMResponse: ...

    @abstractmethod
    def generate_json(self, messages: List[LLMMessage], schema: dict[str, Any], **kwargs) -> dict[str, Any]: ...
