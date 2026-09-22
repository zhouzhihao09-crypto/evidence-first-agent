from __future__ import annotations

from typing import Any, Optional

from evidence_first.models import Action, Observation, Evidence


def record_action(store: Any, run_id: str, action: Action) -> None:
    store.save_action(run_id, action)


def record_observation(store: Any, observation: Observation) -> None:
    store.save_observation(observation)


def record_evidence(store: Any, evidence: Evidence) -> None:
    store.save_evidence(evidence)


def record_verification(store: Any, verification: Any) -> None:
    store.save_verification(verification)


def record_conclusion(store: Any, conclusion: Any) -> None:
    store.save_conclusion(conclusion)


def record_approval(store: Any, approval: Any) -> None:
    store.save_approval(approval)


def add_graph_edge(store: Any, source_id: str, source_type: str, target_id: str, target_type: str, relationship: str) -> None:
    from evidence_first.models import GraphEdge
    edge = GraphEdge(
        source_id=source_id, source_type=source_type,
        target_id=target_id, target_type=target_type,
        relationship=relationship,
    )
    store.add_edge(edge)
