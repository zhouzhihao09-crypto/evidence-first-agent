from __future__ import annotations

import hashlib
from typing import Any, Optional

from evidence_first.models import Action, Observation, Evidence, Verification
from evidence_first.tools.base import Tool


class Verifier:
    def __init__(self):
        self._verifiers: dict[str, object] = {}

    def register_verifier(self, tool_name: str, verifier_fn: Any) -> None:
        self._verifiers[tool_name] = verifier_fn

    def verify(self, action: Action, observation: Observation, evidence: Optional[Evidence] = None, tool: Optional[Tool] = None) -> Verification:
        verifier_fn = self._verifiers.get(action.tool)
        if verifier_fn:
            return self._custom_verify(action, observation, verifier_fn, evidence)
        if tool and hasattr(tool, "verify") and tool.can_verify():
            return tool.verify(action, observation.content)
        return self._default_verify(action, observation, evidence)

    def _custom_verify(self, action: Action, observation: Observation, verifier_fn: Any, evidence: Optional[Evidence] = None) -> Verification:
        result = verifier_fn(action, observation, evidence=evidence)
        evidence_ids = []
        if evidence:
            evidence_ids.append(evidence.evidence_id)
        if isinstance(result, Verification):
            return result
        return Verification(
            action_id=action.action_id,
            observation_id=observation.observation_id,
            evidence_ids=evidence_ids,
            verified=result.get("verified", False),
            confidence=result.get("confidence", 0.0),
            reason=result.get("reason", "Custom verification"),
            status=result.get("status", "EVIDENCE_NOT_FOUND"),
        )

    def _default_verify(self, action: Action, observation: Observation, evidence: Optional[Evidence] = None) -> Verification:
        evidence_ids = []
        if evidence:
            evidence_ids.append(evidence.evidence_id)

        content = (observation.content or "").lower()
        success = observation.metadata.get("success")

        if success is True:
            verified = True
            confidence = 0.9
            reason = "Action completed successfully."
        elif success is False:
            verified = False
            confidence = 0.9
            reason = "Action failed."
        else:
            if "found" in content or "success" in content or "yes" in content:
                verified = True
                confidence = 0.7
                reason = "Observation suggests positive outcome."
            elif "error" in content or "not found" in content or "fail" in content:
                verified = False
                confidence = 0.7
                reason = "Observation suggests negative outcome."
            else:
                verified = False
                confidence = 0.5
                reason = "Could not verify from observation alone."

        return Verification(
            action_id=action.action_id,
            observation_id=observation.observation_id,
            evidence_ids=evidence_ids,
            verified=verified,
            confidence=confidence,
            reason=reason,
            status="EVIDENCE_NOT_FOUND" if not verified else "VERIFIED",
        )

    @staticmethod
    def compute_hash(content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()[:16]
