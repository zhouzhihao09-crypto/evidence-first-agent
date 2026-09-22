from __future__ import annotations

from typing import Optional

from evidence_first.models import Action, Observation
from evidence_first.tools.base import ToolResult


class Executor:
    def __init__(self):
        self._tools: dict[str, object] = {}

    def register_tool(self, tool: object) -> None:
        self._tools[tool.name] = tool

    def execute(self, action: Action) -> Optional[Observation]:
        tool = self._tools.get(action.tool)
        if tool is None:
            return Observation(
                observation_id="obs_" + action.action_id.replace("act_", "obs_")[:12],
                action_id=action.action_id,
                content=f"Tool not found: {action.tool}",
            )
        result: ToolResult = tool.execute(action)
        obs = Observation(
            action_id=action.action_id,
            content=result.content,
            metadata={
                "success": result.success,
                "error": result.error,
                **(result.metadata or {}),
            },
        )
        return obs
