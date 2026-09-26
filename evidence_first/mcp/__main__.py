"""Allow ``python -m evidence_first.mcp`` as an alias for the MCP server."""

from evidence_first.mcp.server import _cli

if __name__ == "__main__":
    _cli()
