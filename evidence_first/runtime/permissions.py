from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# Permission levels
READ = "READ"
WRITE = "WRITE"
ANALYZE = "ANALYZE"
CREATE = "CREATE"
MODIFY = "MODIFY"
SEND = "SEND"
SUBMIT = "SUBMIT"
DELETE = "DELETE"

ALL_PERMISSIONS = [READ, ANALYZE, CREATE, MODIFY, SEND, SUBMIT, DELETE]

# Risk levels
LOW_RISK = "LOW"
MEDIUM_RISK = "MEDIUM"
HIGH_RISK = "HIGH"


# Maps tool names / action types to required permission level and risk
PERMISSION_MAP: dict[str, dict[str, str]] = {
    "filesystem": {"permission": READ, "risk": LOW_RISK},
    "document_search": {"permission": READ, "risk": LOW_RISK},
    "file_create": {"permission": CREATE, "risk": LOW_RISK},
    "http": {"permission": SEND, "risk": MEDIUM_RISK},
    "email": {"permission": SEND, "risk": HIGH_RISK},
    "submit": {"permission": SUBMIT, "risk": HIGH_RISK},
    "delete": {"permission": DELETE, "risk": HIGH_RISK},
    "modify_external": {"permission": MODIFY, "risk": HIGH_RISK},
}

DEFAULT_PERMISSION = READ
DEFAULT_RISK = LOW_RISK


def get_required_permission(action_type: str, tool_name: str) -> str:
    key = tool_name
    if key in PERMISSION_MAP:
        return PERMISSION_MAP[key]["permission"]
    key2 = action_type
    if key2 in PERMISSION_MAP:
        return PERMISSION_MAP[key2]["permission"]
    return DEFAULT_PERMISSION


def get_risk_level(action_type: str, tool_name: str) -> str:
    key = tool_name
    if key in PERMISSION_MAP:
        return PERMISSION_MAP[key]["risk"]
    key2 = action_type
    if key2 in PERMISSION_MAP:
        return PERMISSION_MAP[key2]["risk"]
    return DEFAULT_RISK


# Permission hierarchy (higher index = higher privilege)
PERMISSION_HIERARCHY = {
    READ: 1,
    WRITE: 2,
    ANALYZE: 3,
    CREATE: 4,
    MODIFY: 5,
    SEND: 6,
    SUBMIT: 7,
    DELETE: 8,
}


def permission_level(value: str) -> int:
    return PERMISSION_HIERARCHY.get(value, 0)


def requires_approval(permission: str) -> bool:
    return permission_level(permission) >= permission_level(SEND)


def can_execute_with(available_permission: str, required_permission: str) -> bool:
    return permission_level(available_permission) >= permission_level(required_permission)
