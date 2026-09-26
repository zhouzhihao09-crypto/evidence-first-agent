import json
import os
import sqlite3
import time
from typing import Any, Optional

from evidence_first.models import (
    Action, Observation, Evidence, Verification, Conclusion, Approval, Run, GraphEdge, Claim,
)
from evidence_first.redaction import redact


def _redact_structure(value: Any) -> Any:
    """Recursively redact strings inside JSON-ish structures."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_structure(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_structure(v) for v in value]
    return value


class EvidenceStore:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.path.join(os.getcwd(), ".evidence_first", "evidence.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        c = self.conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            task_id TEXT,
            task_description TEXT,
            status TEXT,
            started_at TEXT,
            completed_at TEXT,
            results TEXT,
            approval_requests TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS actions (
            action_id TEXT PRIMARY KEY,
            run_id TEXT,
            type TEXT,
            tool TEXT,
            input TEXT,
            reason TEXT,
            timestamp TEXT,
            status TEXT,
            output TEXT,
            permission_required TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS observations (
            observation_id TEXT PRIMARY KEY,
            action_id TEXT,
            content TEXT,
            timestamp TEXT,
            metadata TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS evidence_items (
            evidence_id TEXT PRIMARY KEY,
            observation_id TEXT,
            action_id TEXT,
            content TEXT,
            source_type TEXT,
            filename TEXT,
            page INTEGER,
            snippet TEXT,
            hash TEXT,
            url TEXT,
            timestamp TEXT,
            metadata TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS verifications (
            verification_id TEXT PRIMARY KEY,
            action_id TEXT,
            observation_id TEXT,
            evidence_ids TEXT,
            verified INTEGER,
            confidence REAL,
            reason TEXT,
            status TEXT,
            timestamp TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS conclusions (
            conclusion_id TEXT PRIMARY KEY,
            requirement_id TEXT,
            verification_id TEXT,
            status TEXT,
            reason TEXT,
            evidence_ids TEXT,
            timestamp TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS approvals (
            approval_id TEXT PRIMARY KEY,
            action_id TEXT,
            run_id TEXT,
            requested_permission TEXT,
            reason TEXT,
            status TEXT,
            requested_at TEXT,
            decided_at TEXT,
            decision TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS graph_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT,
            source_type TEXT,
            target_id TEXT,
            target_type TEXT,
            relationship TEXT,
            timestamp TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS claims (
            claim_id TEXT PRIMARY KEY,
            run_id TEXT,
            statement TEXT,
            status TEXT,
            evidence_ids TEXT,
            verification_id TEXT,
            check_spec TEXT,
            created_at TEXT,
            verified_at TEXT
        )""")
        self.conn.commit()

    def _dict_from_row(self, row: sqlite3.Row, columns: list[str]) -> dict[str, Any]:
        return {c: row[c] for c in columns if c in row.keys()}

    # --- Runs ---
    def save_run(self, run: Run) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?)",
            (run.run_id, run.task_id, run.task_description, run.status,
             run.started_at, run.completed_at, json.dumps(run.results),
             json.dumps(run.approval_requests)),
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        if result.get("results"):
            result["results"] = json.loads(result["results"])
        if result.get("approval_requests"):
            result["approval_requests"] = json.loads(result["approval_requests"])
        return result

    # --- Actions ---
    def save_action(self, run_id: str, action: Action) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO actions VALUES (?,?,?,?,?,?,?,?,?,?)",
            (action.action_id, run_id, action.type, action.tool,
             json.dumps(_redact_structure(action.input)), redact(action.reason),
             action.timestamp, action.status, redact(action.output or ""),
             action.permission_required),
        )
        self.conn.commit()

    def get_actions(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM actions WHERE run_id=? ORDER BY timestamp", (run_id,)
        ).fetchall()
        return [self._action_from_row(r) for r in rows]

    def _action_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        if d.get("input"):
            d["input"] = json.loads(d["input"])
        return d

    # --- Observations ---
    def save_observation(self, observation: Observation) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO observations VALUES (?,?,?,?,?)",
            (observation.observation_id, observation.action_id,
             redact(observation.content), observation.timestamp,
             json.dumps(_redact_structure(observation.metadata))),
        )
        self.conn.commit()

    def get_observations(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("""
            SELECT o.* FROM observations o
            JOIN actions a ON o.action_id = a.action_id
            WHERE a.run_id=? ORDER BY o.timestamp
        """, (run_id,)).fetchall()
        return [dict(r) for r in rows]

    # --- Evidence ---
    def save_evidence(self, evidence: Evidence) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO evidence_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (evidence.evidence_id, evidence.observation_id, evidence.action_id,
             redact(evidence.content), evidence.source_type, evidence.filename, evidence.page,
             redact(evidence.snippet) if evidence.snippet else evidence.snippet,
             evidence.hash, evidence.url, evidence.timestamp,
             json.dumps(_redact_structure(evidence.metadata))),
        )
        self.conn.commit()

    def get_evidence(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("""
            SELECT e.* FROM evidence_items e
            JOIN actions a ON e.action_id = a.action_id
            WHERE a.run_id=? ORDER BY e.timestamp
        """, (run_id,)).fetchall()
        return [dict(r) for r in rows]

    # --- Verifications ---
    def save_verification(self, verification: Verification) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO verifications VALUES (?,?,?,?,?,?,?,?,?)",
            (verification.verification_id, verification.action_id,
             verification.observation_id, json.dumps(verification.evidence_ids),
             1 if verification.verified else 0, verification.confidence,
             redact(verification.reason), verification.status, verification.timestamp),
        )
        self.conn.commit()

    def get_verifications(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("""
            SELECT v.* FROM verifications v
            JOIN actions a ON v.action_id = a.action_id
            WHERE a.run_id=? ORDER BY v.timestamp
        """, (run_id,)).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if d.get("evidence_ids"):
                d["evidence_ids"] = json.loads(d["evidence_ids"])
            results.append(d)
        return results

    # --- Conclusions ---
    def save_conclusion(self, conclusion: Conclusion) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO conclusions VALUES (?,?,?,?,?,?,?)",
            (conclusion.conclusion_id, conclusion.requirement_id,
             conclusion.verification_id, conclusion.status, redact(conclusion.reason),
             json.dumps(conclusion.evidence_ids), conclusion.timestamp),
        )
        self.conn.commit()

    def get_conclusions(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("""
            SELECT c.* FROM conclusions c
            JOIN actions a ON c.verification_id = (
                SELECT verification_id FROM verifications v WHERE v.action_id = a.action_id LIMIT 1
            )
            WHERE a.run_id=? ORDER BY c.timestamp
        """, (run_id,)).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if d.get("evidence_ids"):
                d["evidence_ids"] = json.loads(d["evidence_ids"])
            results.append(d)
        return results

    # --- Approvals ---
    def save_approval(self, approval: Approval) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO approvals VALUES (?,?,?,?,?,?,?,?,?)",
            (approval.approval_id, approval.action_id, approval.run_id,
             approval.requested_permission, approval.reason, approval.status,
             approval.requested_at, approval.decided_at, approval.decision),
        )
        self.conn.commit()

    def get_pending_approvals(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM approvals WHERE run_id=? AND status='pending'", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_approvals(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM approvals WHERE run_id=? ORDER BY requested_at", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Claims ---
    def save_claim(self, claim: Claim) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO claims VALUES (?,?,?,?,?,?,?,?,?)",
            (claim.claim_id, claim.run_id, redact(claim.statement), claim.status,
             json.dumps(claim.evidence_ids), claim.verification_id,
             json.dumps(_redact_structure(claim.check)), claim.created_at,
             claim.verified_at),
        )
        self.conn.commit()

    def _claim_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["check"] = {}
        for source, target in (("evidence_ids", "evidence_ids"), ("check_spec", "check")):
            if d.get(source):
                try:
                    d[target] = json.loads(d[source])
                except (TypeError, ValueError):
                    d[target] = {} if target == "check" else []
        return d

    def get_claim(self, claim_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM claims WHERE claim_id=?", (claim_id,)
        ).fetchone()
        return self._claim_from_row(row) if row else None

    def get_claims(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM claims WHERE run_id=? ORDER BY created_at, claim_id", (run_id,)
        ).fetchall()
        return [self._claim_from_row(r) for r in rows]

    def get_claim_verifications(self, run_id: str) -> list[dict[str, Any]]:
        """Verifications recorded against claims of a run.

        Claim verifications are not attached to an action, so they are not
        returned by :meth:`get_verifications` (which joins through actions).
        """
        rows = self.conn.execute(
            """SELECT v.* FROM verifications v
               JOIN claims c ON c.verification_id = v.verification_id
               WHERE c.run_id=? ORDER BY v.timestamp, v.verification_id""",
            (run_id,),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if d.get("evidence_ids"):
                try:
                    d["evidence_ids"] = json.loads(d["evidence_ids"])
                except (TypeError, ValueError):
                    d["evidence_ids"] = []
            results.append(d)
        return results

    def update_approval(self, approval_id: str, decision: str) -> None:
        self.conn.execute(
            "UPDATE approvals SET status=?, decision=?, decided_at=? WHERE approval_id=?",
            ("decided", decision, __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), approval_id),
        )
        self.conn.commit()

    # --- Graph ---
    def add_edge(self, edge: GraphEdge) -> None:
        self.conn.execute(
            "INSERT INTO graph_edges (source_id, source_type, target_id, target_type, relationship, timestamp) VALUES (?,?,?,?,?,?)",
            (edge.source_id, edge.source_type, edge.target_id, edge.target_type, edge.relationship, edge.timestamp),
        )
        self.conn.commit()

    def get_edges(self, run_id: str = None, source_id: str = None, source_type: str = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM graph_edges WHERE 1=1"
        params = []
        # Note: graph_edges doesn't have run_id; filter by source/target IDs
        if source_id:
            query += " AND source_id=?"
            params.append(source_id)
        if source_type:
            query += " AND source_type=?"
            params.append(source_type)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_graph_for_run(self, run_id: str) -> dict[str, list[dict[str, Any]]]:
        nodes = {"actions": [], "observations": [], "evidence": [], "verifications": [], "conclusions": []}
        actions = self.get_actions(run_id)
        for a in actions:
            nodes["actions"].append({"id": a["action_id"], "type": "action", "data": a})
        observations = self.get_observations(run_id)
        for o in observations:
            nodes["observations"].append({"id": o["observation_id"], "type": "observation", "data": o})
        evidence = self.get_evidence(run_id)
        for e in evidence:
            nodes["evidence"].append({"id": e["evidence_id"], "type": "evidence", "data": e})
        verifications = self.get_verifications(run_id)
        for v in verifications:
            nodes["verifications"].append({"id": v["verification_id"], "type": "verification", "data": v})
        conclusions = self.get_conclusions(run_id)
        for c in conclusions:
            nodes["conclusions"].append({"id": c["conclusion_id"], "type": "conclusion", "data": c})
        edges = self.conn.execute(
            "SELECT * FROM graph_edges"
        ).fetchall()
        return {"nodes": nodes, "edges": [dict(r) for r in edges]}

    # --- Cleanup ---
    def close(self) -> None:
        self.conn.close()
