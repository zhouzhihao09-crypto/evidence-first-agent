from __future__ import annotations

import re
from typing import Any, Optional

from evidence_first.models import Action, Evidence, Observation, Verification

EVIDENCE_NOT_FOUND = "EVIDENCE_NOT_FOUND"
EVIDENCE_FOUND_BUT_INSUFFICIENT = "EVIDENCE_FOUND_BUT_INSUFFICIENT"
VERIFIED = "VERIFIED"
NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"

REQUIREMENT_STRATEGIES: dict[str, dict[str, Any]] = {
    "insurance": {
        "strategy": "validity_date",
        "tender_deadline": "2025-06-01",
        "positive_keywords": ["active", "valid", "tender coverage", "valid for tender"],
        "date_label": "Expiry Date",
        "fallback_status": NEEDS_HUMAN_REVIEW,
    },
    "tax": {
        "strategy": "supporting_statement",
        "positive_keywords": ["compliant", "no outstanding", "up to date"],
        "fallback_status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
    },
    "tax compliance": {
        "strategy": "supporting_statement",
        "positive_keywords": ["compliant", "no outstanding", "up to date"],
        "fallback_status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
    },
    "license": {
        "strategy": "supporting_statement",
        "positive_keywords": ["current", "valid", "active"],
        "fallback_status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
    },
    "business license": {
        "strategy": "supporting_statement",
        "positive_keywords": ["current", "valid", "active"],
        "fallback_status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
    },
    "financial statement": {
        "strategy": "supporting_statement",
        "positive_keywords": ["unqualified", "audited", "accurate", "fair view"],
        "fallback_status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
    },
    "audit report": {
        "strategy": "supporting_statement",
        "positive_keywords": ["unqualified", "clean", "no material"],
        "fallback_status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
    },
    "project experience": {
        "strategy": "count_check",
        "min_count": 3,
        "count_label": "Total Similar Projects",
        "fallback_status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
    },
    "iso 9001": {
        "strategy": "supporting_statement",
        "positive_keywords": ["valid", "certified", "certification", "iso 9001"],
        "fallback_status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
    },
    "quality": {
        "strategy": "supporting_statement",
        "positive_keywords": ["valid", "certified", "certification", "iso 9001"],
        "fallback_status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
    },
    "safety": {
        "strategy": "supporting_statement",
        "positive_keywords": ["0 lost time", "0 incidents", "passed", "no major"],
        "fallback_status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
    },
    "safety record": {
        "strategy": "supporting_statement",
        "positive_keywords": ["0 lost time", "0 incidents", "passed", "no major"],
        "fallback_status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
    },
    "environmental compliance": {
        "strategy": "supporting_statement",
        "positive_keywords": ["compliant", "no violations", "valid"],
        "fallback_status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
    },
    "environmental": {
        "strategy": "supporting_statement",
        "positive_keywords": ["compliant", "no violations", "valid"],
        "fallback_status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
    },
    "bond": {
        "strategy": "supporting_statement",
        "positive_keywords": ["active", "valid", "issued"],
        "fallback_status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
    },
    "surety": {
        "strategy": "supporting_statement",
        "positive_keywords": ["active", "valid", "issued"],
        "fallback_status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
    },
    "surety bond": {
        "strategy": "supporting_statement",
        "positive_keywords": ["active", "valid", "issued"],
        "fallback_status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
    },
}


def _date_from_text(content: str, label: str) -> Optional[str]:
    patterns = [
        rf"{re.escape(label)}\s*[:：]\s*(\d{{4}}[-\/]\d{{2}}[-\/]\d{{2}})",
        rf"{re.escape(label)}\s*[:：]\s*(\d{{1,2}}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{{4}})",
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _normalize_date(date_str: str) -> Optional[str]:
    date_str = date_str.strip()
    formats = ["%Y-%m-%d", "%Y/%m/%d"]
    for fmt in formats:
        try:
            from datetime import datetime
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _count_from_text(content: str, label: str) -> Optional[int]:
    patterns = [
        rf"{re.escape(label)}\s*[:：]\s*(\d+)",
        rf"Total\s+{re.escape(label)}\s*[:：]\s*(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _find_best_excerpt(content: str, positive_keywords: list[str]) -> Optional[str]:
    lines = content.splitlines()
    best = None
    best_score = 0
    for line in lines:
        lower = line.lower()
        score = sum(1 for kw in positive_keywords if kw in lower)
        if score > best_score:
            best_score = score
            best = line.strip()
    return best


def _verify_supporting_statement(result: dict[str, Any], strategy: dict[str, Any], doc_content: str) -> dict[str, Any]:
    keywords = strategy["positive_keywords"]
    excerpt = _find_best_excerpt(doc_content, keywords)
    if excerpt:
        return {
            "verified": True,
            "confidence": 0.9,
            "reason": f"Supporting statement found: '{excerpt[:120]}'",
            "status": VERIFIED,
            "excerpt": excerpt,
        }
    return {
        "verified": False,
        "confidence": 0.6,
        "reason": "Document found but no supporting statement identified for this requirement.",
        "status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
        "excerpt": None,
    }


def _verify_validity_date(result: dict[str, Any], strategy: dict[str, Any], doc_content: str) -> dict[str, Any]:
    tender_deadline = strategy.get("tender_deadline", "")
    date_str = _date_from_text(doc_content, strategy.get("date_label", "Expiry Date"))
    if date_str:
        normalized = _normalize_date(date_str)
        if normalized and tender_deadline:
            if normalized >= tender_deadline:
                return {
                    "verified": True,
                    "confidence": 0.95,
                    "reason": f"Expiry date {normalized} is on or after tender deadline {tender_deadline}",
                    "status": VERIFIED,
                    "excerpt": f"Expiry: {date_str}",
                }
            else:
                return {
                    "verified": False,
                    "confidence": 0.85,
                    "reason": f"Expiry date {normalized} is before tender deadline {tender_deadline}",
                    "status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
                    "excerpt": f"Expiry: {date_str}",
                }
    positive_keywords = strategy.get("positive_keywords", [])
    excerpt = _find_best_excerpt(doc_content, positive_keywords)
    if excerpt:
        return {
            "verified": True,
            "confidence": 0.75,
            "reason": f"Document states: '{excerpt[:120]}' but no validity date extracted for comparison.",
            "status": VERIFIED,
            "excerpt": excerpt,
        }
    return {
        "verified": False,
        "confidence": 0.6,
        "reason": "Document found but no validity date or supporting statement identified.",
        "status": NEEDS_HUMAN_REVIEW,
        "excerpt": None,
    }


def _verify_count_check(result: dict[str, Any], strategy: dict[str, Any], doc_content: str) -> dict[str, Any]:
    min_count = strategy.get("min_count", 3)
    count_str = _count_from_text(doc_content, strategy.get("count_label", "Total"))
    if count_str is not None:
        if count_str >= min_count:
            return {
                "verified": True,
                "confidence": 0.9,
                "reason": f"Document states {count_str} projects, meeting minimum requirement of {min_count}",
                "status": VERIFIED,
                "excerpt": f"Count: {count_str}",
            }
        else:
            return {
                "verified": False,
                "confidence": 0.85,
                "reason": f"Document states {count_str} projects, below minimum requirement of {min_count}",
                "status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
                "excerpt": f"Count: {count_str}",
            }
    positive_keywords = strategy.get("positive_keywords", [])
    excerpt = _find_best_excerpt(doc_content, positive_keywords)
    if excerpt:
        return {
            "verified": False,
            "confidence": 0.6,
            "reason": "Document found but unable to extract project count for verification.",
            "status": NEEDS_HUMAN_REVIEW,
            "excerpt": None,
        }
    return {
        "verified": False,
        "confidence": 0.5,
        "reason": "No project count evidence found.",
        "status": EVIDENCE_FOUND_BUT_INSUFFICIENT,
    }


VERIFY_STRATEGIES = {
    "supporting_statement": _verify_supporting_statement,
    "validity_date": _verify_validity_date,
    "count_check": _verify_count_check,
}


def _find_matching_query(
    query: str,
) -> Optional[tuple[str, dict[str, Any]]]:
    query_lower = query.lower()
    for key, strategy in REQUIREMENT_STRATEGIES.items():
        if key in query_lower or query_lower in key:
            return key, strategy
    return None, {}


def _analyze_search_results(observation: Observation) -> list[dict[str, Any]]:
    results = observation.metadata.get("results", []) if observation.metadata else []
    return results if isinstance(results, list) else []


def make_verification_for_tender(
    action: Action,
    observation: Observation,
    evidence: Optional[Evidence] = None,
) -> dict[str, Any]:
    content = observation.content or ""
    query = action.input.get("query", "").lower() if action.input else ""
    results = _analyze_search_results(observation)

    if not results:
        return {
            "verified": False,
            "confidence": 0.9,
            "reason": "No evidence found for requirement: no matching documents.",
            "status": EVIDENCE_NOT_FOUND,
            "excerpt": None,
        }

    match_key, strategy = _find_matching_query(query)
    if not strategy:
        strategy = {"strategy": "supporting_statement", "positive_keywords": ["compliant", "valid", "current", "active"], "fallback_status": NEEDS_HUMAN_REVIEW}

    best_result = None
    best_verdict = None
    for result in results:
        doc_content = result.get("content", "")
        if not doc_content:
            continue
        verifier_fn = VERIFY_STRATEGIES.get(strategy.get("strategy", "supporting_statement"))
        if verifier_fn:
            verdict = verifier_fn({"content": doc_content}, strategy, doc_content)
        else:
            verdict = {
                "verified": False,
                "confidence": 0.5,
                "reason": "Unknown verification strategy.",
                "status": NEEDS_HUMAN_REVIEW,
                "excerpt": None,
            }
        if verdict.get("status") == VERIFIED:
            best_result = result
            best_verdict = verdict
            break
        if best_verdict is None or (
            verdict.get("status") == EVIDENCE_FOUND_BUT_INSUFFICIENT
            and best_verdict.get("status") != VERIFIED
        ):
            best_result = result
            best_verdict = verdict

    if best_verdict is None:
        return {
            "verified": False,
            "confidence": 0.5,
            "reason": "No document content available for verification.",
            "status": EVIDENCE_NOT_FOUND,
            "excerpt": None,
        }

    return {
        "verified": best_verdict["verified"],
        "confidence": best_verdict["confidence"],
        "reason": best_verdict["reason"],
        "status": best_verdict["status"],
        "excerpt": best_verdict.get("excerpt"),
    }


class TenderVerifier:
    @staticmethod
    def verify(action: Action, observation: Observation, evidence: Optional[Evidence] = None) -> dict[str, Any]:
        return make_verification_for_tender(action, observation, evidence=evidence)
