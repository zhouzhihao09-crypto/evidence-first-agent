"""Secret redaction for evidence, claims, and receipts.

Receipts are portable documents: they get copied around, pasted into issues,
and handed to auditors. Nothing credential-shaped should ever reach one.

This module is intentionally dependency-free and pattern-based. It is applied
on write (so secrets are not persisted) and on receipt rendering (so legacy
rows written before redaction existed still cannot leak into a receipt).
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

REDACTED = "[REDACTED]"

#: (name, pattern, keep_group). ``keep_group`` is the group whose text is kept
#: verbatim; everything else in the match is replaced with ``[REDACTED]``.
#: ``None`` means the whole match is the secret.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str], Optional[int]], ...] = (
    (
        "private_key_block",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        None,
    ),
    (
        "authorization_header",
        re.compile(r"(?i)\b(authorization\s*:\s*(?:bearer|basic|token)\s+)([^\s\"',;]{3,})"),
        1,
    ),
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), None),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"), None),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), None),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), None),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b"), None),
)

#: ``key = value`` / ``"key": "value"`` forms for credential-shaped names.
CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)([\"']?\b(?:api[_-]?key|apikey|access[_-]?key|secret[_-]?key|client[_-]?secret"
    r"|secret|token|password|passwd|pwd|passphrase|private[_-]?key|credential)\b[\"']?"
    r"\s*[:=]\s*)([\"']?)([^\s\"',;}\]]{3,})([\"']?)"
)

#: Environment-variable assignment that exports a credential-shaped value.
ENV_ASSIGNMENT = re.compile(
    r"(?im)^(\s*(?:export\s+)?[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)S?"
    r"[A-Z0-9_]*\s*=\s*)(?![\"']?$)(\S.*)$"
)


def find_secrets(text: str) -> list[str]:
    """Return the names of secret patterns detected in ``text``."""
    if not text:
        return []
    found: list[str] = []
    for name, pattern, _ in SECRET_PATTERNS:
        if pattern.search(text):
            found.append(name)
    if CREDENTIAL_ASSIGNMENT.search(text):
        found.append("credential_assignment")
    if ENV_ASSIGNMENT.search(text):
        found.append("env_credential_assignment")
    return found


def has_secret(text: str) -> bool:
    return bool(find_secrets(text))


def redact(text: str) -> str:
    """Replace credential-shaped substrings with ``[REDACTED]``."""
    if not text:
        return text
    result = text
    for _, pattern, keep_group in SECRET_PATTERNS:
        if keep_group is None:
            result = pattern.sub(REDACTED, result)
        else:
            result = pattern.sub(lambda m, g=keep_group: f"{m.group(g)}{REDACTED}", result)
    result = CREDENTIAL_ASSIGNMENT.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}{m.group(4)}", result
    )
    result = ENV_ASSIGNMENT.sub(lambda m: f"{m.group(1)}{REDACTED}", result)
    return result


def redact_values(values: Iterable[str]) -> list[str]:
    return [redact(v) for v in values]
