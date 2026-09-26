"""Deterministic claim verification.

Two responsibilities, both kept out of the distribution adapters (CLI, MCP):

1. Canonical claim states (:data:`VERIFIED`, :data:`FAILED`,
   :data:`INSUFFICIENT_EVIDENCE`, :data:`NOT_VERIFIED`) plus a lossless mapping
   from the pre-existing tender verification states.
2. A small registry of deterministic checks. A check answers a claim from
   recorded evidence or from the filesystem — never from a model's opinion.

No probabilistic confidence is introduced here: the existing
``Verification.confidence`` field is preserved untouched, and new claim
verifications report a state plus a reason.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# --- Canonical claim states -------------------------------------------------
VERIFIED = "VERIFIED"
FAILED = "FAILED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
NOT_VERIFIED = "NOT_VERIFIED"

CLAIM_STATES = (VERIFIED, FAILED, INSUFFICIENT_EVIDENCE, NOT_VERIFIED)

#: Pre-existing verification states (evidence_first.agent.tender_verifier)
#: mapped onto the canonical claim states. Mapping is one-way and lossless:
#: the original state is always kept alongside the mapped one.
LEGACY_STATUS_MAP: dict[str, str] = {
    "VERIFIED": VERIFIED,
    "EVIDENCE_FOUND_BUT_INSUFFICIENT": INSUFFICIENT_EVIDENCE,
    "EVIDENCE_NOT_FOUND": INSUFFICIENT_EVIDENCE,
    "NEEDS_HUMAN_REVIEW": NOT_VERIFIED,
}


def to_claim_state(status: Optional[str], verified: Optional[bool] = None) -> str:
    """Map an engine verification state onto a canonical claim state."""
    if status and status in CLAIM_STATES:
        return status
    if status and status in LEGACY_STATUS_MAP:
        return LEGACY_STATUS_MAP[status]
    if verified is True:
        return VERIFIED
    if verified is False:
        return FAILED
    return NOT_VERIFIED


def aggregate_claim_state(states: list[str]) -> str:
    """Reduce many claim states into one run-level result state."""
    if not states:
        return NOT_VERIFIED
    if any(s == FAILED for s in states):
        return FAILED
    if any(s in (INSUFFICIENT_EVIDENCE, NOT_VERIFIED) for s in states):
        return NEEDS_REVIEW
    return VERIFIED


#: Run-level result states (receipt.result.status)
NEEDS_REVIEW = "NEEDS_REVIEW"

RESULT_VERIFIED = VERIFIED
RESULT_FAILED = FAILED
RESULT_NEEDS_REVIEW = NEEDS_REVIEW


@dataclass
class CheckResult:
    status: str
    reason: str
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass
class CheckContext:
    """Everything a check is allowed to look at."""

    run_id: str
    claim: dict[str, Any]
    evidence: list[dict[str, Any]]
    verifications: list[dict[str, Any]]
    workdir: str = ""

    def evidence_text(self) -> str:
        parts: list[str] = []
        for item in self.evidence:
            for key in ("content", "snippet", "description"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    parts.append(value)
        return "\n".join(parts)

    def resolve_path(self, path: str) -> str:
        """Resolve ``path`` under the allowed working directory.

        Reuses the same restriction as ``FilesystemTool``: a claim check must
        not become a way to read files outside the workspace.
        """
        root = os.path.abspath(self.workdir or os.getcwd())
        candidate = path if os.path.isabs(path) else os.path.join(root, path)
        abs_path = os.path.abspath(candidate)
        if not (abs_path == root or abs_path.startswith(root + os.sep)):
            raise PermissionError(
                f"Path {path} is outside allowed working directory {root}"
            )
        return abs_path


CheckFn = Callable[[CheckContext, dict[str, Any]], CheckResult]
_CHECKS: dict[str, CheckFn] = {}


def register_check(name: str, fn: CheckFn) -> None:
    _CHECKS[name] = fn


def available_checks() -> list[str]:
    return sorted(_CHECKS)


def get_check(name: str) -> Optional[CheckFn]:
    return _CHECKS.get(name)


# --- Built-in deterministic checks -----------------------------------------

def _check_evidence_count(ctx: CheckContext, spec: dict[str, Any]) -> CheckResult:
    minimum = int(spec.get("min_count", 1))
    found = len(ctx.evidence)
    ids = [e.get("evidence_id", "") for e in ctx.evidence if e.get("evidence_id")]
    if found >= minimum:
        return CheckResult(
            VERIFIED,
            f"Found {found} evidence record(s); at least {minimum} required.",
            ids,
        )
    return CheckResult(
        INSUFFICIENT_EVIDENCE,
        f"Found {found} evidence record(s); {minimum} required.",
        ids,
    )


def _check_keyword_present(ctx: CheckContext, spec: dict[str, Any]) -> CheckResult:
    keyword = str(spec.get("keyword", ""))
    if not keyword:
        return CheckResult(NOT_VERIFIED, "No keyword supplied for keyword_present check.")
    case_sensitive = bool(spec.get("case_sensitive", False))
    haystack = ctx.evidence_text()
    needle = keyword if case_sensitive else keyword.lower()
    probe = haystack if case_sensitive else haystack.lower()
    ids = [e.get("evidence_id", "") for e in ctx.evidence if e.get("evidence_id")]
    if needle in probe:
        return CheckResult(VERIFIED, f"Keyword {keyword!r} found in recorded evidence.", ids)
    if not ctx.evidence:
        return CheckResult(
            INSUFFICIENT_EVIDENCE,
            f"No evidence recorded, cannot look for {keyword!r}.",
            [],
        )
    return CheckResult(FAILED, f"Keyword {keyword!r} not found in recorded evidence.", ids)


def _check_keyword_absent(ctx: CheckContext, spec: dict[str, Any]) -> CheckResult:
    keyword = str(spec.get("keyword", ""))
    if not keyword:
        return CheckResult(NOT_VERIFIED, "No keyword supplied for keyword_absent check.")
    case_sensitive = bool(spec.get("case_sensitive", False))
    haystack = ctx.evidence_text()
    probe = haystack if case_sensitive else haystack.lower()
    needle = keyword if case_sensitive else keyword.lower()
    ids = [e.get("evidence_id", "") for e in ctx.evidence if e.get("evidence_id")]
    if not ctx.evidence:
        return CheckResult(
            INSUFFICIENT_EVIDENCE,
            f"No evidence recorded, cannot confirm absence of {keyword!r}.",
            [],
        )
    if needle in probe:
        return CheckResult(FAILED, f"Keyword {keyword!r} present in recorded evidence.", ids)
    return CheckResult(VERIFIED, f"Keyword {keyword!r} absent from recorded evidence.", ids)


def _check_file_exists(ctx: CheckContext, spec: dict[str, Any]) -> CheckResult:
    path = str(spec.get("path", ""))
    if not path:
        return CheckResult(NOT_VERIFIED, "No path supplied for file_exists check.")
    try:
        resolved = ctx.resolve_path(path)
    except PermissionError as exc:
        return CheckResult(FAILED, str(exc))
    if os.path.isfile(resolved):
        return CheckResult(VERIFIED, f"File exists: {path}")
    return CheckResult(FAILED, f"File does not exist: {path}")


def _check_sha256_match(ctx: CheckContext, spec: dict[str, Any]) -> CheckResult:
    path = str(spec.get("path", ""))
    expected = str(spec.get("sha256", "")).lower().removeprefix("sha256:")
    if not path or not expected:
        return CheckResult(NOT_VERIFIED, "sha256_match requires 'path' and 'sha256'.")
    try:
        resolved = ctx.resolve_path(path)
    except PermissionError as exc:
        return CheckResult(FAILED, str(exc))
    if not os.path.isfile(resolved):
        return CheckResult(FAILED, f"File does not exist: {path}")
    with open(resolved, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    if digest == expected:
        return CheckResult(VERIFIED, f"SHA-256 of {path} matches recorded value.")
    return CheckResult(FAILED, f"SHA-256 of {path} is {digest}, expected {expected}.")


def _check_verification_status_equals(ctx: CheckContext, spec: dict[str, Any]) -> CheckResult:
    expected = to_claim_state(str(spec.get("status", VERIFIED)))
    query_contains = str(spec.get("query_contains", "")).lower()
    considered: list[dict[str, Any]] = []
    for verification in ctx.verifications:
        if query_contains and query_contains not in json_dumps_lower(verification):
            continue
        considered.append(verification)
    if not considered:
        return CheckResult(
            INSUFFICIENT_EVIDENCE,
            "No verification results available to evaluate this claim.",
            [],
        )
    matched: list[str] = []
    for verification in considered:
        if to_claim_state(verification.get("status"), verification.get("verified")) == expected:
            matched.append(verification.get("verification_id", ""))
    if matched:
        return CheckResult(
            VERIFIED,
            f"{len(matched)} verification result(s) reached {expected}.",
            matched,
        )
    return CheckResult(
        FAILED,
        f"No verification result reached {expected}.",
        [v.get("verification_id", "") for v in considered],
    )


def json_dumps_lower(value: Any) -> str:
    import json

    try:
        return json.dumps(value, default=str).lower()
    except (TypeError, ValueError):
        return str(value).lower()


register_check("evidence_count", _check_evidence_count)
register_check("keyword_present", _check_keyword_present)
register_check("keyword_absent", _check_keyword_absent)
register_check("file_exists", _check_file_exists)
register_check("sha256_match", _check_sha256_match)
register_check("verification_status_equals", _check_verification_status_equals)


def verify_claim(claim: dict[str, Any], ctx: CheckContext) -> CheckResult:
    """Run the claim's deterministic check.

    A claim without a check is never auto-verified: the honest answer is
    ``NOT_VERIFIED`` with a reason, not a guess.
    """
    spec = claim.get("check") or {}
    if not isinstance(spec, dict) or not spec.get("type"):
        return CheckResult(
            NOT_VERIFIED,
            "No deterministic check defined for this claim; it cannot be auto-verified.",
            [],
        )
    check_type = str(spec["type"])
    fn = get_check(check_type)
    if fn is None:
        return CheckResult(
            NOT_VERIFIED,
            f"Unknown check type {check_type!r}; available: {', '.join(available_checks())}.",
            [],
        )
    return fn(ctx, spec)
