import json
import re
from typing import Any, List, Optional

from evidence_first.llm.base import LLMProvider, LLMMessage, LLMResponse


class MockLLMProvider(LLMProvider):
    def __init__(self, responses: Optional[List[str]] = None):
        self._responses = responses or []
        self._index = 0
        self._name = "mock"

    @property
    def name(self) -> str:
        return self._name

    def set_responses(self, responses: List[str]) -> None:
        self._responses = responses
        self._index = 0

    def generate(self, messages: List[LLMMessage], **kwargs) -> LLMResponse:
        if self._index < len(self._responses):
            response = self._responses[self._index]
            self._index += 1
            return LLMResponse(content=response, model="mock")
        return LLMResponse(content="", model="mock")

    def generate_json(self, messages: List[LLMMessage], schema: dict[str, Any], **kwargs) -> dict[str, Any]:
        text = self.generate(messages).content
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            return {}
