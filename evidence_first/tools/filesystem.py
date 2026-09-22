import os
from typing import Any

from evidence_first.tools.base import Tool, ToolResult
from evidence_first.models import Action


class FilesystemTool(Tool):
    @property
    def name(self) -> str:
        return "filesystem"

    @property
    def description(self) -> str:
        return "Read files, list directories, and check file existence in allowed paths"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File or directory path"},
                "operation": {
                    "type": "string",
                    "enum": ["read", "list", "exists", "info"],
                    "description": "Operation to perform",
                },
            },
            "required": ["operation"],
        }

    @property
    def permission_required(self) -> str:
        return "READ"

    @property
    def risk_level(self) -> str:
        return "LOW"

    def _resolve_path(self, path: str) -> str:
        abs_path = os.path.abspath(path)
        cwd = os.getcwd()
        if not abs_path.startswith(cwd):
            raise PermissionError(
                f"Path {path} is outside allowed working directory {cwd}"
            )
        return abs_path

    def execute(self, action: Action) -> ToolResult:
        params = action.input
        op = params.get("operation", "read")
        path = params.get("path", "")

        try:
            resolved = self._resolve_path(path)
        except PermissionError as e:
            return ToolResult(success=False, error=str(e))

        if op == "read":
            return self._read(resolved)
        elif op == "list":
            return self._list(resolved)
        elif op == "exists":
            return self._exists(resolved)
        elif op == "info":
            return self._info(resolved)
        else:
            return ToolResult(success=False, error=f"Unknown operation: {op}")

    def _read(self, path: str) -> ToolResult:
        if not os.path.isfile(path):
            return ToolResult(
                success=False, error=f"Not a file: {path}"
            )
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            return ToolResult(
                success=True,
                content=content,
                metadata={"filename": os.path.basename(path), "path": path},
            )
        except UnicodeDecodeError:
            return ToolResult(
                success=False, error=f"Binary file, cannot read as text: {path}"
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def _list(self, path: str) -> ToolResult:
        if not os.path.isdir(path):
            return ToolResult(success=False, error=f"Not a directory: {path}")
        try:
            entries = os.listdir(path)
            files = []
            for entry in sorted(entries):
                full = os.path.join(path, entry)
                info = {
                    "name": entry,
                    "is_dir": os.path.isdir(full),
                    "size": os.path.getsize(full) if os.path.isfile(full) else 0,
                }
                files.append(info)
            return ToolResult(success=True, content=str(files), metadata={"path": path, "entries": files})
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def _exists(self, path: str) -> ToolResult:
        exists = os.path.exists(path)
        return ToolResult(success=True, content=str(exists), metadata={"exists": exists})

    def _info(self, path: str) -> ToolResult:
        if not os.path.exists(path):
            return ToolResult(success=False, error=f"Path does not exist: {path}")
        stat = os.stat(path)
        return ToolResult(
            success=True,
            content=f"{'directory' if os.path.isdir(path) else 'file'}: {os.path.basename(path)}",
            metadata={
                "path": path,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "is_dir": os.path.isdir(path),
            },
        )
