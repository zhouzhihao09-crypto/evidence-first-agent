from __future__ import annotations

import os
from typing import Any, List, Optional

from evidence_first.llm.base import LLMProvider, LLMMessage
from evidence_first.models import Action, Task

REQUIREMENT_KEYWORDS = [
    ("insurance", "Valid insurance certificate"),
    ("tax compliance", "Tax compliance certificate"),
    ("business license", "Business license"),
    ("financial statement", "Financial statements"),
    ("audit report", "Audited financial report"),
    ("project experience", "Evidence of past project experience"),
    ("ISO 9001", "Quality management certification"),
    ("safety record", "Health and safety record"),
    ("environmental compliance", "Environmental compliance certificate"),
    ("surety bond", "Surety bond or financial guarantee"),
]


class Planner:
    def __init__(self, llm: Optional[LLMProvider] = None):
        self._llm = llm

    def plan(self, task: Task, available_tools: List[str] = None) -> List[dict[str, Any]]:
        if self._llm is None:
            return self._default_plan(task, available_tools)
        return self._llm_plan(task, available_tools)

    def _default_plan(self, task: Task, available_tools: List[str] = None) -> List[dict[str, Any]]:
        steps = [
            {
                "step": 1,
                "action_type": "read_file",
                "tool": "filesystem",
                "description": "Read the requirements document",
                "input": {"operation": "read", "path": "examples/tender/requirements.md"},
                "reason": "Need to understand all tender requirements before searching for evidence.",
                "permission_required": "READ",
            },
        ]
        for keyword, description in REQUIREMENT_KEYWORDS:
            steps.append({
                "step": len(steps) + 1,
                "action_type": "search_documents",
                "tool": "document_search",
                "description": f"Search for: {description}",
                "input": {"query": keyword, "directory": "examples/tender/company_docs"},
                "reason": f"Need to find supporting evidence for requirement: {description}",
                "permission_required": "READ",
            })
        return steps

    def _llm_plan(self, task: Task, available_tools: List[str] = None) -> List[dict[str, Any]]:
        tool_list = ", ".join(available_tools or [])
        messages = [
            LLMMessage("system", f"You are a planning agent. Given the task, break it into actionable steps using available tools: {tool_list}"),
            LLMMessage("user", f"Task: {task.description}"),
        ]
        response = self._llm.generate(messages)
        import json
        try:
            return json.loads(response.content)
        except json.JSONDecodeError:
            return self._default_plan(task, available_tools)
