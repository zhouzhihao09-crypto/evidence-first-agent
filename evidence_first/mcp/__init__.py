"""MCP adapter over the Evidence-First core.

MCP is a distribution mechanism, not the product. Every tool delegates to
:class:`evidence_first.work.WorkLedger` (and therefore to the existing
permission, approval, recording and verification code). No verification
decision is made in this module, and no tool can bypass an approval gate.

``create_server`` and ``main`` are imported lazily so that
``python -m evidence_first.mcp.server`` does not import the module twice.
"""

from typing import Any

__all__ = ["create_server", "main"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from evidence_first.mcp import server

        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
