"""AI Work Receipt — a portable, hashable summary of one work run.

A receipt is a versioned JSON document. Its canonical serialization is stable:
the same logical receipt always produces the same bytes, therefore the same
SHA-256 hash. That is what makes a receipt checkable — anyone can recompute the
hash and tell whether the receipt was altered.

Canonicalization rules (documented, tested, and stable):

1. ``integrity`` is removed before hashing; the hash never hashes itself.
2. JSON is serialized with ``sort_keys=True`` and ``(",", ":")`` separators, so
   field order in the builder cannot change the digest.
3. Lists are explicitly ordered by ``(timestamp, id)`` before serialization.
4. The canonical string is encoded as UTF-8 before hashing.
5. No value is generated during hashing. Timestamps come from the run record.

Receipts never carry credentials: evidence bodies are reduced to a snippet and
a hash, and every string is passed through :mod:`evidence_first.redaction`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from evidence_first.redaction import redact
from evidence_first.verification import (
    CLAIM_STATES,
    FAILED,
    INSUFFICIENT_EVIDENCE,
    NEEDS_REVIEW,
    NOT_VERIFIED,
    RESULT_FAILED,
    RESULT_NEEDS_REVIEW,
    RESULT_VERIFIED,
    VERIFIED,
    to_claim_state,
)

RECEIPT_VERSION = "1"
HASH_ALGORITHM = "sha256"
SNIPPET_LIMIT = 200


# --- Canonical serialization -------------------------------------------------

def canonical_json(payload: Any) -> str:
    """Stable JSON text for hashing and for on-disk receipt export."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


def canonical_bytes(payload: Any) -> bytes:
    return canonical_json(payload).encode("utf-8")


def compute_receipt_hash(receipt: dict[str, Any]) -> str:
    """SHA-256 of the canonical receipt payload, excluding ``integrity``."""
    payload = {k: v for k, v in receipt.items() if k != "integrity"}
    digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    return f"{HASH_ALGORITHM}:{digest}"


def seal_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """Attach the integrity block to a receipt and return it."""
    receipt = dict(receipt)
    receipt.pop("integrity", None)
    receipt["integrity"] = {
        "algorithm": HASH_ALGORITHM,
        "hash": compute_receipt_hash(receipt),
    }
    return receipt


def verify_receipt_hash(receipt: dict[str, Any]) -> bool:
    integrity = receipt.get("integrity") or {}
    expected = integrity.get("hash")
    if not expected:
        return False
    return compute_receipt_hash(receipt) == expected


# --- Receipt construction ----------------------------------------------------

def _sort_key(item: dict[str, Any], id_key: str) -> tuple:
    return (str(item.get("timestamp") or item.get("created_at") or ""), str(item.get(id_key) or ""))


def _evidence_entry(item: dict[str, Any]) -> dict[str, Any]:
    snippet = redact(item.get("snippet") or "")
    if len(snippet) > SNIPPET_LIMIT:
        snippet = snippet[:SNIPPET_LIMIT] + "..."
    return {
        "evidence_id": item.get("evidence_id", ""),
        "action_id": item.get("action_id", ""),
        "type": item.get("source_type", "document"),
        "source": item.get("filename") or item.get("url") or "",
        "hash": item.get("hash") or "",
        "snippet": snippet,
        "timestamp": item.get("timestamp", ""),
    }


def _action_entry(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "action_id": item.get("action_id", ""),
        "type": item.get("type", ""),
        "tool": item.get("tool", ""),
        "status": item.get("status", ""),
        "permission_required": item.get("permission_required", ""),
        "reason": redact(item.get("reason") or ""),
        "input": item.get("input", {}) if isinstance(item.get("input"), dict) else {},
        "timestamp": item.get("timestamp", ""),
    }


def _derived_statement(action: dict[str, Any]) -> str:
    action_input = action.get("input") if isinstance(action.get("input"), dict) else {}
    query = action_input.get("query") or action_input.get("path") or ""
    if action.get("type") == "search_documents" and query:
        return f"Evidence supports requirement: {query}"
    if query:
        return f"Step recorded: {action.get('type', '')} {query}".strip()
    return f"Step recorded: {action.get('type', '')} via {action.get('tool', '')}".strip()


def _derived_claims(
    actions: list[dict[str, Any]],
    verifications: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project legacy runs (tender workflow) into claims.

    Runs created before claims existed still carry one verification per
    requirement. Each verification is surfaced as a *derived* claim so a
    receipt can summarize those runs truthfully, using the verification engine's
    own status rather than any new judgement.
    """
    by_action = {a.get("action_id", ""): a for a in actions}
    claims: list[dict[str, Any]] = []
    for verification in verifications:
        action = by_action.get(verification.get("action_id", ""), {})
        state = to_claim_state(verification.get("status"), verification.get("verified"))
        claims.append(
            {
                "claim_id": verification.get("verification_id", ""),
                "statement": _derived_statement(action),
                "status": state,
                "source": "derived",
                "verification_id": verification.get("verification_id", ""),
                "evidence_ids": list(verification.get("evidence_ids") or []),
                "engine_status": verification.get("status", ""),
            }
        )
    return claims


def _explicit_claims(claims: list[dict[str, Any]], verifications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_verification = {v.get("verification_id", ""): v for v in verifications}
    entries: list[dict[str, Any]] = []
    for claim in claims:
        verification = by_verification.get(claim.get("verification_id") or "", {})
        state = claim.get("status", NOT_VERIFIED)
        if verification:
            state = to_claim_state(verification.get("status"), verification.get("verified"))
        entries.append(
            {
                "claim_id": claim.get("claim_id", ""),
                "statement": redact(claim.get("statement", "")),
                "status": state,
                "source": "explicit",
                "verification_id": claim.get("verification_id") or "",
                "evidence_ids": list(claim.get("evidence_ids") or []),
                "check": claim.get("check") or {},
                "engine_status": verification.get("status", ""),
            }
        )
    return entries


def _verification_entry(item: dict[str, Any], claim_id_by_verification: dict[str, str]) -> dict[str, Any]:
    verification_id = item.get("verification_id", "")
    return {
        "verification_id": verification_id,
        "action_id": item.get("action_id", ""),
        "claim_id": claim_id_by_verification.get(verification_id, ""),
        "status": item.get("status", ""),
        "claim_state": to_claim_state(item.get("status"), item.get("verified")),
        "verified": bool(item.get("verified")),
        "confidence": item.get("confidence", 0.0),
        "reason": redact(item.get("reason") or ""),
        "evidence_ids": list(item.get("evidence_ids") or []),
        "timestamp": item.get("timestamp", ""),
    }


def _approval_entry(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "approval_id": item.get("approval_id", ""),
        "action_id": item.get("action_id", ""),
        "requested_permission": item.get("requested_permission", ""),
        "reason": redact(item.get("reason") or ""),
        "status": item.get("status", ""),
        "requested_at": item.get("requested_at", ""),
        "decided_at": item.get("decided_at") or "",
        "decision": item.get("decision") or "",
    }


def build_receipt(store: Any, run_id: str) -> Optional[dict[str, Any]]:
    """Assemble and seal the work receipt for ``run_id``.

    Returns ``None`` when the run does not exist.
    """
    run = store.get_run(run_id)
    if not run:
        return None

    actions = sorted(store.get_actions(run_id), key=lambda a: _sort_key(a, "action_id"))
    evidence = sorted(store.get_evidence(run_id), key=lambda e: _sort_key(e, "evidence_id"))
    verifications = sorted(
        store.get_verifications(run_id), key=lambda v: _sort_key(v, "verification_id")
    )
    known = {v.get("verification_id", "") for v in verifications}
    for claim_verification in store.get_claim_verifications(run_id):
        if claim_verification.get("verification_id", "") not in known:
            verifications.append(claim_verification)
    verifications.sort(key=lambda v: _sort_key(v, "verification_id"))
    approvals = sorted(store.get_approvals(run_id), key=lambda a: (a.get("requested_at", ""), a.get("approval_id", "")))
    stored_claims = store.get_claims(run_id)

    claims = _explicit_claims(stored_claims, verifications) if stored_claims else _derived_claims(actions, verifications)
    claim_id_by_verification = {
        c.get("verification_id", ""): c.get("claim_id", "")
        for c in claims
        if c.get("verification_id")
    }

    results = run.get("results") or {}
    if not isinstance(results, dict):
        results = {}
    pending_approvals = [a for a in approvals if a.get("status") == "pending"]

    counts = {state: 0 for state in CLAIM_STATES}
    for claim in claims:
        counts[claim["status"]] = counts.get(claim["status"], 0) + 1

    result_status = _result_status(
        claim_statuses=[c["status"] for c in claims],
        run_status=run.get("status", ""),
        pending_approvals=len(pending_approvals),
    )

    receipt: dict[str, Any] = {
        "version": RECEIPT_VERSION,
        "run_id": run.get("run_id", run_id),
        "run": {
            "task_id": run.get("task_id", ""),
            "agent": redact(str(results.get("agent") or "unspecified")),
            "task": redact(run.get("task_description", "")),
            "objective": redact(str(results.get("objective") or "")),
            "status": run.get("status", ""),
            "started_at": run.get("started_at", ""),
            "finished_at": results.get("finished_at") or run.get("completed_at") or "",
        },
        "actions": [_action_entry(a) for a in actions],
        "evidence": [_evidence_entry(e) for e in evidence],
        "claims": claims,
        "verifications": [_verification_entry(v, claim_id_by_verification) for v in verifications],
        "approvals": [_approval_entry(a) for a in approvals],
        "result": {
            "status": result_status,
            "claims_total": len(claims),
            "verified": counts.get(VERIFIED, 0),
            "insufficient_evidence": counts.get(INSUFFICIENT_EVIDENCE, 0),
            "not_verified": counts.get(NOT_VERIFIED, 0),
            "failed": counts.get(FAILED, 0),
            "pending_approvals": len(pending_approvals),
            "evidence_count": len(evidence),
            "action_count": len(actions),
        },
    }
    return seal_receipt(receipt)


def _result_status(
    claim_statuses: list[str],
    run_status: str,
    pending_approvals: int,
) -> str:
    """Run-level outcome, derived only from recorded states.

    Nothing here guesses: a run with no claims is never reported as VERIFIED.
    """
    if run_status == "failed":
        return RESULT_FAILED
    if FAILED in claim_statuses:
        return RESULT_FAILED
    if pending_approvals:
        return RESULT_NEEDS_REVIEW
    if not claim_statuses:
        return RESULT_NEEDS_REVIEW
    if all(state == VERIFIED for state in claim_statuses):
        return RESULT_VERIFIED
    return RESULT_NEEDS_REVIEW


# --- Human-readable rendering ------------------------------------------------

_STATE_MARKS = {
    VERIFIED: "[OK]",
    RESULT_VERIFIED: "[OK]",
    FAILED: "[!!]",
    INSUFFICIENT_EVIDENCE: "[--]",
    NOT_VERIFIED: "[??]",
    NEEDS_REVIEW: "[??]",
}


def _mark(state: str) -> str:
    return _STATE_MARKS.get(state, "[..]")


def render_receipt_text(receipt: dict[str, Any], width: int = 64) -> str:
    """Render a receipt as a terminal-friendly AI WORK RECEIPT block."""
    run = receipt.get("run", {})
    result = receipt.get("result", {})
    rule = "-" * width
    lines: list[str] = []
    lines.append("AI WORK RECEIPT")
    lines.append(rule)
    lines.append(f"Run:        {receipt.get('run_id', '')}")
    lines.append(f"Agent:      {run.get('agent', '')}")
    lines.append(f"Task:       {run.get('task', '')}")
    if run.get("objective"):
        lines.append(f"Objective:  {run['objective']}")
    lines.append(f"Started:    {run.get('started_at', '')}")
    lines.append(f"Finished:   {run.get('finished_at', '') or '-'}")
    lines.append(f"Run status: {run.get('status', '')}")

    lines.append("")
    lines.append(f"ACTIONS ({len(receipt.get('actions', []))})")
    for action in receipt.get("actions", []):
        lines.append(
            f"  - {action.get('type', '')} via {action.get('tool', '')} "
            f"[{action.get('status', '')}] {action.get('permission_required', '')}"
        )
    if not receipt.get("actions"):
        lines.append("  (none)")

    lines.append("")
    lines.append(f"EVIDENCE ({len(receipt.get('evidence', []))})")
    for item in receipt.get("evidence", []):
        source = item.get("source") or item.get("type", "")
        digest = (item.get("hash") or "")[:16]
        lines.append(f"  - {item.get('type', '')} {source} {digest}".rstrip())
    if not receipt.get("evidence"):
        lines.append("  (none)")

    lines.append("")
    lines.append(f"CLAIMS ({len(receipt.get('claims', []))})")
    for claim in receipt.get("claims", []):
        lines.append(f"  {_mark(claim.get('status', ''))} {claim.get('statement', '')}")
    if not receipt.get("claims"):
        lines.append("  (none)")

    lines.append("")
    lines.append(f"VERIFICATIONS ({len(receipt.get('verifications', []))})")
    for verification in receipt.get("verifications", []):
        lines.append(
            f"  {_mark(verification.get('claim_state', ''))} "
            f"{verification.get('status', '')}: {verification.get('reason', '')}"
        )
    if not receipt.get("verifications"):
        lines.append("  (none)")

    lines.append("")
    lines.append(f"APPROVALS ({len(receipt.get('approvals', []))})")
    for approval in receipt.get("approvals", []):
        lines.append(
            f"  [{approval.get('status', '')}] {approval.get('requested_permission', '')} "
            f"- {approval.get('reason', '')}"
        )
    if not receipt.get("approvals"):
        lines.append("  (none)")

    lines.append("")
    lines.append("RESULT")
    lines.append(f"  Claims:     {result.get('claims_total', 0)}")
    lines.append(f"  Verified:   {result.get('verified', 0)}")
    lines.append(f"  Insufficient: {result.get('insufficient_evidence', 0)}")
    lines.append(f"  Not verified: {result.get('not_verified', 0)}")
    lines.append(f"  Failed:     {result.get('failed', 0)}")
    if result.get("pending_approvals"):
        lines.append(f"  Pending approvals: {result['pending_approvals']}")
    lines.append(f"  STATUS:     {result.get('status', '')}")

    integrity = receipt.get("integrity", {})
    lines.append("")
    lines.append("RECEIPT HASH")
    lines.append(f"  {integrity.get('hash', '')}")
    lines.append(f"  version: {receipt.get('version', RECEIPT_VERSION)}")
    return "\n".join(lines)
