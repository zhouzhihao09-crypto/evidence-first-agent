from __future__ import annotations

import os
import tempfile

import pytest

from evidence_first.evidence.store import EvidenceStore
from evidence_first.web import (
    app, esc, get_status_counts, get_requirements_for_run,
    get_strategy_for_requirement, _list_runs, set_db_path, get_store, DB_PATH,
)
from fastapi.testclient import TestClient


def _make_store(db_path=None):
    path = db_path or os.path.join(tempfile.mkdtemp(), "test.db")
    return EvidenceStore(db_path=path)


@pytest.fixture
def client():
    db_dir = tempfile.mkdtemp()
    db_path = os.path.join(db_dir, "test.db")
    set_db_path(db_path)
    store = EvidenceStore(db_path=db_path)
    from evidence_first.runtime.run import AgentRuntime
    from evidence_first.tools.filesystem import FilesystemTool
    from evidence_first.tools.search import DocumentSearchTool
    from evidence_first.llm.mock import MockLLMProvider
    runtime = AgentRuntime(llm=MockLLMProvider(), tools=[FilesystemTool(), DocumentSearchTool()], auto_approve=True, store=store)
    run = runtime.run_task("Determine whether a company has the documents required for a tender")
    runtime.store.close()
    store.close()
    try:
        c = TestClient(app)
        yield c
    finally:
        set_db_path(os.path.join(os.getcwd(), ".evidence_first", "evidence.db"))
        import shutil
        shutil.rmtree(db_dir, ignore_errors=True)


class TestRunsEndpoint:
    def test_runs_returns_persisted_runs(self, client):
        response = client.get("/api/runs")
        assert response.status_code == 200
        data = response.json()
        assert "runs" in data
        assert len(data["runs"]) >= 1

    def test_run_detail_returns_correct_run(self, client):
        runs_resp = client.get("/api/runs")
        run_id = runs_resp.json()["runs"][0]["run_id"]
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        data = response.json()
        assert "run" in data
        assert data["run"]["run_id"] == run_id
        assert "actions" in data
        assert "evidence" in data
        assert "verifications" in data

    def test_missing_run_id_handled_safely(self, client):
        response = client.get("/api/runs/nonexistent_run_999")
        assert response.status_code == 404


class TestRequirementsEndpoint:
    def test_requirements_returns_verifications(self, client):
        runs_resp = client.get("/api/runs")
        run_id = runs_resp.json()["runs"][0]["run_id"]
        response = client.get(f"/api/runs/{run_id}/requirements")
        assert response.status_code == 200
        data = response.json()
        assert "requirements" in data
        assert len(data["requirements"]) >= 1
        for req in data["requirements"]:
            assert "query" in req
            assert "status" in req
            assert "confidence" in req
            assert "reason" in req

    def test_verification_status_preserved(self, client):
        runs_resp = client.get("/api/runs")
        run_id = runs_resp.json()["runs"][0]["run_id"]
        response = client.get(f"/api/runs/{run_id}/requirements")
        data = response.json()
        statuses = [r["status"] for r in data["requirements"]]
        assert "VERIFIED" in statuses or "EVIDENCE_FOUND_BUT_INSUFFICIENT" in statuses


class TestEvidenceEndpoint:
    def test_evidence_returns_records(self, client):
        runs_resp = client.get("/api/runs")
        run_id = runs_resp.json()["runs"][0]["run_id"]
        response = client.get(f"/api/runs/{run_id}/evidence")
        assert response.status_code == 200
        data = response.json()
        assert "evidence" in data
        assert len(data["evidence"]) >= 1
        for e in data["evidence"]:
            assert "evidence_id" in e
            assert "action_id" in e

    def test_missing_run_evidence_handled_safely(self, client):
        response = client.get("/api/runs/nonexistent/evidence")
        assert response.status_code == 404


class TestTenderRunVerification:
    def test_tender_run_has_verified_and_insufficient(self, client):
        runs_resp = client.get("/api/runs")
        run_id = runs_resp.json()["runs"][0]["run_id"]
        response = client.get(f"/api/runs/{run_id}/requirements")
        data = response.json()
        statuses = [r["status"] for r in data["requirements"]]
        verified_count = statuses.count("VERIFIED")
        insufficient_count = statuses.count("EVIDENCE_FOUND_BUT_INSUFFICIENT")
        assert verified_count >= 1, f"Expected at least 1 VERIFIED, got {verified_count}"
        assert insufficient_count >= 1, f"Expected at least 1 INSUFFICIENT, got {insufficient_count}"

    def test_insurance_is_insufficient(self, client):
        runs_resp = client.get("/api/runs")
        run_id = runs_resp.json()["runs"][0]["run_id"]
        response = client.get(f"/api/runs/{run_id}/requirements")
        data = response.json()
        insurance_reqs = [r for r in data["requirements"] if "insurance" in r["query"].lower()]
        assert len(insurance_reqs) >= 1
        for req in insurance_reqs:
            assert req["status"] == "EVIDENCE_FOUND_BUT_INSUFFICIENT", f"Insurance should be EVIDENCE_FOUND_BUT_INSUFFICIENT, got {req['status']}"
            assert "Expiry" in req["reason"] or "expiry" in req["reason"].lower() or "before" in req["reason"].lower()

    def test_insurance_evidence_shows_expiry_and_deadline(self, client):
        runs_resp = client.get("/api/runs")
        run_id = runs_resp.json()["runs"][0]["run_id"]
        requirements_resp = client.get(f"/api/runs/{run_id}/requirements")
        insurance_req = [r for r in requirements_resp.json()["requirements"] if "insurance" in r["query"].lower()][0]
        action_id = insurance_req["action_id"]
        response = client.get(f"/runs/{run_id}/requirements/{action_id}")
        assert response.status_code == 200
        content = response.text
        assert "2024-12-31" in content, "Should show insurance expiry date"
        assert "2025-06-01" in content, "Should show tender deadline"
        assert "EVIDENCE_FOUND_BUT_INSUFFICIENT" in content, "Should show insufficient status"
        assert "deterministically" in content.lower() or "fails" in content.lower(), "Should explain deterministic failure"


class TestStatusCounts:
    def test_verified_count_correct(self, client):
        runs_resp = client.get("/api/runs")
        run_id = runs_resp.json()["runs"][0]["run_id"]
        response = client.get(f"/api/runs/{run_id}")
        data = response.json()
        counts = data["verification_counts"]
        assert counts["VERIFIED"] >= 9, f"Expected at least 9 VERIFIED, got {counts['VERIFIED']}"
        assert counts["EVIDENCE_FOUND_BUT_INSUFFICIENT"] >= 1, f"Expected at least 1 INSUFFICIENT, got {counts['EVIDENCE_FOUND_BUT_INSUFFICIENT']}"


class TestHTMLPages:
    def test_dashboard_returns_html(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        content = response.text
        assert "Runs" in content or "No runs found" in content

    def test_run_detail_page_returns_html(self, client):
        runs_resp = client.get("/api/runs")
        run_id = runs_resp.json()["runs"][0]["run_id"]
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200
        content = response.text
        assert "Requirement" in content or "Run not found" in content
        assert "VERIFIED" in content or "EVIDENCE_FOUND_BUT_INSUFFICIENT" in content

    def test_requirement_detail_page_returns_html(self, client):
        runs_resp = client.get("/api/runs")
        run_id = runs_resp.json()["runs"][0]["run_id"]
        reqs_resp = client.get(f"/api/runs/{run_id}/requirements")
        action_id = reqs_resp.json()["requirements"][0]["action_id"]
        response = client.get(f"/runs/{run_id}/requirements/{action_id}")
        assert response.status_code == 200
        content = response.text
        assert "Requirement" in content

    def test_missing_run_page_handled_safely(self, client):
        response = client.get("/runs/nonexistent_run_999")
        assert response.status_code == 200
        assert "not found" in response.text.lower()

    def test_missing_requirement_handled_safely(self, client):
        runs_resp = client.get("/api/runs")
        run_id = runs_resp.json()["runs"][0]["run_id"]
        response = client.get(f"/runs/{run_id}/requirements/nonexistent_action")
        assert response.status_code == 200


class TestApprovalsPage:
    def test_approvals_page_returns_html(self, client):
        runs_resp = client.get("/api/runs")
        run_id = runs_resp.json()["runs"][0]["run_id"]
        response = client.get(f"/runs/{run_id}/approvals")
        assert response.status_code == 200
        content = response.text
        assert "Approval Queue" in content or "No pending" in content


class TestStoreFunctions:
    def test_get_status_counts(self):
        store = _make_store()
        try:
            from evidence_first.models import Action, Observation, Verification
            action = Action(action_id="act_1", type="search_documents", tool="document_search", input={"query": "test"})
            store.save_action("run_1", action)
            obs = Observation(observation_id="obs_1", action_id="act_1", content="test", metadata={"results": []})
            store.save_observation(obs)
            ver = Verification(
                verification_id="ver_1", action_id="act_1", observation_id="obs_1",
                evidence_ids=[], verified=True, confidence=0.9, reason="test",
                status="VERIFIED", timestamp="2024-01-01T00:00:00+00:00",
            )
            store.save_verification(ver)
            counts = get_status_counts("run_1", store)
            assert counts["VERIFIED"] == 1
            assert counts["EVIDENCE_FOUND_BUT_INSUFFICIENT"] == 0
            assert counts["NEEDS_HUMAN_REVIEW"] == 0
            assert counts["EVIDENCE_NOT_FOUND"] == 0
        finally:
            store.close()

    def test_get_requirements_for_run(self):
        store = _make_store()
        try:
            from evidence_first.models import Action, Observation
            action = Action(action_id="act_1", type="search_documents", tool="document_search", input={"query": "insurance", "directory": "/docs"})
            store.save_action("run_1", action)
            obs = Observation(observation_id="obs_1", action_id="act_1", content="Found 1", metadata={"results": []})
            store.save_observation(obs)
            reqs = get_requirements_for_run("run_1", store)
            assert len(reqs) == 1
            assert reqs[0]["query"] == "insurance"
            assert reqs[0]["status"] == "EVIDENCE_NOT_FOUND"
        finally:
            store.close()

    def test_esc(self):
        assert esc("<script>alert('xss')</script>") == "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"
        assert esc(None) == ""
        assert esc("normal") == "normal"

    def test_list_runs(self):
        store = _make_store()
        try:
            from evidence_first.models import Action, Run
            run = Run(run_id="run_1", task_id="task_1", task_description="Test")
            store.save_run(run)
            action = Action(action_id="act_1", type="search_documents", tool="document_search", input={"query": "test"})
            store.save_action("run_1", action)
            runs = _list_runs(store)
            assert len(runs) == 1
            assert runs[0]["run_id"] == "run_1"
            assert "verification_counts" in runs[0]
        finally:
            store.close()
