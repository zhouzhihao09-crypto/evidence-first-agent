import os
import hashlib
from typing import Any

from evidence_first.tools.base import Tool, ToolResult
from evidence_first.models import Action, Evidence


class DocumentSearchTool(Tool):
    @property
    def name(self) -> str:
        return "document_search"

    @property
    def description(self) -> str:
        return "Search local documents for keywords and extract relevant snippets"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "directory": {"type": "string", "description": "Directory to search"},
                "pattern": {"type": "string", "description": "File pattern (e.g., *.txt)"},
            },
            "required": ["query"],
        }

    @property
    def permission_required(self) -> str:
        return "READ"

    @property
    def risk_level(self) -> str:
        return "LOW"

    def execute(self, action: Action) -> ToolResult:
        query = action.input.get("query", "")
        directory = action.input.get("directory", ".")
        pattern = action.input.get("pattern", "*.txt")

        try:
            resolved_dir = os.path.abspath(directory)
            if not os.path.isdir(resolved_dir):
                return ToolResult(success=False, error=f"Directory not found: {directory}")

            results = []
            for root, dirs, files in os.walk(resolved_dir):
                for fname in sorted(files):
                    if not fname.lower().endswith((".txt", ".md", ".yaml", ".yml", ".json")):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            content = f.read()
                    except Exception:
                        continue

                    lines = content.splitlines()
                    matching_lines = []
                    for i, line in enumerate(lines):
                        if query.lower() in line.lower():
                            matching_lines.append({"line": i + 1, "text": line.strip()})

                    if matching_lines:
                        evidence = Evidence(
                            action_id=action.action_id,
                            content=content,
                            source_type="document",
                            filename=fname,
                            snippet="\n".join(m["text"] for m in matching_lines[:5]),
                            hash=hashlib.sha256(content.encode()).hexdigest()[:16],
                        )
                        results.append(
                            {
                                "file": fname,
                                "path": fpath,
                                "matches": matching_lines,
                                "content": content,
                                "evidence_id": evidence.evidence_id,
                            }
                        )

            summary = f"Found {len(results)} files matching '{query}'"
            return ToolResult(
                success=True,
                content=summary,
                metadata={"results": results, "query": query},
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))
