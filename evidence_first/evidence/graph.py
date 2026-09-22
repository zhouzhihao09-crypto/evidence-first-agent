from __future__ import annotations

from typing import Any, Optional

from evidence_first.models import GraphEdge


class EvidenceGraph:
    def __init__(self, store: Any):
        self._store = store

    def link(self, source_id: str, source_type: str, target_id: str, target_type: str, relationship: str) -> None:
        self._store.add_edge(GraphEdge(
            source_id=source_id, source_type=source_type,
            target_id=target_id, target_type=target_type,
            relationship=relationship,
        ))

    def get_chain(self, run_id: str = None) -> list[dict[str, Any]]:
        return self._store.get_graph_for_run(run_id or "")

    def find_by_requirement(self, requirement_id: str) -> list[dict[str, Any]]:
        edges = self._store.get_edges(source_id=requirement_id)
        return edges
