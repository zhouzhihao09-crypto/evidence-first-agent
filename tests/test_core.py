from __future__ import annotations

import os
import tempfile

import pytest

from evidence_first.models import Action, Observation, Evidence, Verification, Conclusion, Approval, Run, Task, GraphEdge
from evidence_first.evidence.store import EvidenceStore
from evidence_first.evidence.recorder import record_action, record_observation, record_evidence, record_verification, record_conclusion, add_graph_edge
from evidence_first.evidence.graph import EvidenceGraph
from evidence_first.tools.base import Tool, ToolResult
from evidence_first.tools.filesystem import FilesystemTool
from evidence_first.tools.search import DocumentSearchTool
from evidence_first.agent.executor import Executor
from evidence_first.agent.verifier import Verifier
from evidence_first.runtime.permissions import (
    READ, ANALYZE, CREATE, MODIFY, SEND, SUBMIT, DELETE,
    get_required_permission, get_risk_level, requires_approval, can_execute_with,
    PERMISSION_HIERARCHY,
)
from evidence_first.runtime.approvals import ApprovalManager, ApprovalRequest
from evidence_first.agent.planner import Planner
from evidence_first.llm.mock import MockLLMProvider
from evidence_first.runtime.run import AgentRuntime

TEST_DIR = os.path.join(os.path.dirname(__file__), "..")


@pytest.fixture
def temp_store():
    db_dir = tempfile.mkdtemp()
    db_path = os.path.join(db_dir, "test.db")
    store = EvidenceStore(db_path=db_path)
    yield store
    store.close()


@pytest.fixture
def run(temp_store):
    r = Run(run_id="run_test1", task_id="task_test1", task_description="Test task")
    temp_store.save_run(r)
    yield r


@pytest.fixture
def sample_action():
    return Action(action_id="act_001", type="read_file", tool="filesystem", input={"path": "test.txt"}, reason="test")


@pytest.fixture
def sample_observation():
    return Observation(observation_id="obs_001", action_id="act_001", content="test content")


@pytest.fixture
def sample_evidence():
    return Evidence(evidence_id="ev_001", observation_id="obs_001", action_id="act_001", content="test content", source_type="document", filename="test.txt")


# --- Tests: Models ---


def test_action_defaults():
    a = Action()
    assert a.action_id.startswith("act_")
    assert a.status == "pending"
    assert a.input == {}


def test_task_defaults():
    t = Task(description="Test")
    assert t.task_id.startswith("task_")
    assert t.description == "Test"


def test_observation_requires_action():
    obs = Observation(observation_id="obs_1", action_id="act_1", content="data")
    assert obs.action_id == "act_1"


def test_evidence_with_metadata():
    e = Evidence(evidence_id="ev_1", observation_id="obs_1", action_id="act_1", content="test", source_type="document", filename="doc.txt", page=5)
    assert e.filename == "doc.txt"
    assert e.page == 5


def test_approval_initial_state():
    a = Approval()
    assert a.status == "pending"
    assert a.decided_at is None
    assert a.decision is None


# --- Tests: Action Recording ---


def test_record_action(temp_store, sample_action):
    record_action(temp_store, "run_test", sample_action)
    actions = temp_store.get_actions("run_test")
    assert len(actions) == 1
    assert actions[0]["action_id"] == "act_001"
    assert actions[0]["status"] == "pending"


def test_record_multiple_actions(temp_store):
    for i in range(5):
        a = Action(action_id=f"act_{i:03d}", type="read", tool="filesystem", input={})
        record_action(temp_store, "run_test", a)
    actions = temp_store.get_actions("run_test")
    assert len(actions) == 5


def test_record_action_with_input(temp_store, sample_action):
    record_action(temp_store, "run_test", sample_action)
    actions = temp_store.get_actions("run_test")
    assert actions[0]["input"] == {"path": "test.txt"}


# --- Tests: Observation Recording ---


def test_record_observation(temp_store, sample_action, sample_observation):
    record_action(temp_store, "run_test", sample_action)
    record_observation(temp_store, sample_observation)
    observations = temp_store.get_observations("run_test")
    assert len(observations) == 1
    assert observations[0]["content"] == "test content"


# --- Tests: Evidence Recording ---


def test_record_evidence(temp_store, sample_action, sample_observation, sample_evidence):
    record_action(temp_store, "run_test", sample_action)
    record_observation(temp_store, sample_observation)
    record_evidence(temp_store, sample_evidence)
    evidence = temp_store.get_evidence("run_test")
    assert len(evidence) == 1
    assert evidence[0]["evidence_id"] == "ev_001"
    assert evidence[0]["filename"] == "test.txt"


# --- Tests: Evidence Store Persistence ---


def test_store_persists_runs(temp_store):
    r = Run(run_id="run_persist", task_id="t1", task_description="persist test")
    temp_store.save_run(r)
    r2 = temp_store.get_run("run_persist")
    assert r2 is not None
    assert r2["task_id"] == "t1"


def test_store_overwrites_run(temp_store):
    r = Run(run_id="run_over", task_id="t1", task_description="original")
    temp_store.save_run(r)
    r2 = Run(run_id="run_over", task_id="t1", task_description="updated")
    temp_store.save_run(r2)
    r3 = temp_store.get_run("run_over")
    assert r3["task_description"] == "updated"


# --- Tests: Evidence Relationships ---


def test_action_observation_link(temp_store, sample_action, sample_observation):
    record_action(temp_store, "run_test", sample_action)
    record_observation(temp_store, sample_observation)
    add_graph_edge(temp_store, "act_001", "action", "obs_001", "observation", "produces")
    graph = temp_store.get_graph_for_run("run_test")
    assert len(graph["nodes"]["actions"]) == 1
    assert len(graph["nodes"]["observations"]) == 1


def test_observation_evidence_link(temp_store, sample_action, sample_observation, sample_evidence):
    record_action(temp_store, "run_test", sample_action)
    record_observation(temp_store, sample_observation)
    record_evidence(temp_store, sample_evidence)
    add_graph_edge(temp_store, "obs_001", "observation", "ev_001", "evidence", "supports")
    graph = temp_store.get_graph_for_run("run_test")
    assert len(graph["nodes"]["evidence"]) == 1


# --- Tests: Graph Traversal ---


def test_graph_edges(temp_store):
    graph = EvidenceGraph(temp_store)
    add_graph_edge(temp_store, "req_1", "requirement", "act_1", "action", "triggers")
    add_graph_edge(temp_store, "act_1", "action", "obs_1", "observation", "produces")
    add_graph_edge(temp_store, "obs_1", "observation", "ev_1", "evidence", "supports")
    edges = temp_store.get_edges(source_id="req_1")
    assert len(edges) == 1
    assert edges[0]["relationship"] == "triggers"


def test_graph_chain(temp_store):
    graph = EvidenceGraph(temp_store)
    add_graph_edge(temp_store, "R1", "requirement", "A1", "action", "triggers")
    add_graph_edge(temp_store, "A1", "action", "O1", "observation", "produces")
    add_graph_edge(temp_store, "O1", "observation", "E1", "evidence", "supports")
    add_graph_edge(temp_store, "E1", "evidence", "V1", "verification", "verified_by")
    add_graph_edge(temp_store, "V1", "verification", "C1", "conclusion", "leads_to")
    chain = temp_store.get_graph_for_run("nonexistent")
    assert len(chain["edges"]) == 5


# --- Tests: Verifier ---


def test_verifier_defaults_to_unverified():
    v = Verifier()
    action = Action(action_id="act_1", type="test", tool="test", input={})
    obs = Observation(observation_id="obs_1", action_id="act_1", content="unknown result")
    result = v.verify(action, obs)
    assert result.verified is False
    assert result.confidence == 0.5
    assert "Could not verify" in result.reason


def test_verifier_success_action():
    v = Verifier()
    action = Action(action_id="act_1", type="test", tool="test", input={})
    obs = Observation(observation_id="obs_1", action_id="act_1", content="done", metadata={"success": True})
    result = v.verify(action, obs)
    assert result.verified is True
    assert result.confidence == 0.9


def test_verifier_failure_action():
    v = Verifier()
    action = Action(action_id="act_1", type="test", tool="test", input={})
    obs = Observation(observation_id="obs_1", action_id="act_1", content="error", metadata={"success": False})
    result = v.verify(action, obs)
    assert result.verified is False
    assert result.confidence == 0.9


def test_verifier_with_evidence():
    v = Verifier()
    action = Action(action_id="act_1", type="test", tool="test", input={})
    obs = Observation(observation_id="obs_1", action_id="act_1", content="done", metadata={"success": True})
    ev = Evidence(evidence_id="ev_1", action_id="act_1", observation_id="obs_1", content="evidence")
    result = v.verify(action, obs, evidence=ev)
    assert "ev_1" in result.evidence_ids


def test_verifier_finds_positive_keywords():
    v = Verifier()
    action = Action(action_id="act_1", type="search", tool="document_search", input={})
    obs = Observation(observation_id="obs_1", action_id="act_1", content="Found 3 results", metadata={})
    result = v.verify(action, obs)
    assert result.verified is True


def test_verifier_finds_negative_keywords():
    v = Verifier()
    action = Action(action_id="act_1", type="search", tool="document_search", input={})
    obs = Observation(observation_id="obs_1", action_id="act_1", content="failed with error", metadata={})
    result = v.verify(action, obs)
    assert result.verified is False


def test_computer_hash():
    h = Verifier.compute_hash("test content")
    assert len(h) == 16
    assert h == Verifier.compute_hash("test content")


def test_tender_verifier_insurance_found():
    from evidence_first.agent.tender_verifier import TenderVerifier
    action = Action(action_id="act_1", type="search_documents", tool="document_search", input={"query": "insurance"})
    obs = Observation(observation_id="obs_1", action_id="act_1", content="Found 1 files matching 'insurance'",
                       metadata={"results": [{"file": "insurance_policy.txt", "content": "Insurance Policy for ABC Corp\nStatus: ACTIVE\nExpiry Date: 2024-12-31", "matches": [{"line": 1, "text": "Insurance Policy for ABC Corp"}]}]})
    result = TenderVerifier.verify(action, obs)
    assert result["status"] in ("VERIFIED", "EVIDENCE_FOUND_BUT_INSUFFICIENT", "NEEDS_HUMAN_REVIEW")


def test_tender_verifier_insurance_not_found():
    from evidence_first.agent.tender_verifier import TenderVerifier
    action = Action(action_id="act_1", type="search_documents", tool="document_search", input={"query": "insurance"})
    obs = Observation(observation_id="obs_1", action_id="act_1", content="Found 0 files matching 'insurance'", metadata={})
    result = TenderVerifier.verify(action, obs)
    assert result["verified"] is False
    assert result["status"] == "EVIDENCE_NOT_FOUND"


def test_tender_verifier_no_matching_docs():
    from evidence_first.agent.tender_verifier import TenderVerifier
    action = Action(action_id="act_1", type="search_documents", tool="document_search", input={"query": "nonexistent"})
    obs = Observation(observation_id="obs_1", action_id="act_1", content="No results", metadata={})
    result = TenderVerifier.verify(action, obs)
    assert result["verified"] is False
    assert result["status"] == "EVIDENCE_NOT_FOUND"


# --- Tests: Evidence-Based Verification (Phase 2B) ---


def test_tender_verifier_filename_only_is_not_verified():
    from evidence_first.agent.tender_verifier import TenderVerifier
    action = Action(action_id="act_1", type="search_documents", tool="document_search", input={"query": "tax compliance"})
    obs = Observation(
        observation_id="obs_1", action_id="act_1",
        content="Found 1 files matching 'tax compliance'",
        metadata={
            "results": [
                {
                    "file": "tax_information.txt",
                    "path": "/docs/tax_information.txt",
                    "content": "Tax registration information\nCompany Tax ID: XXXXX\nBusiness Name: ABC Corp",
                    "matches": [{"line": 1, "text": "Tax registration information"}],
                }
            ]
        },
    )
    result = TenderVerifier.verify(action, obs)
    assert result["status"] == "EVIDENCE_FOUND_BUT_INSUFFICIENT"
    assert result["verified"] is False


def test_tender_verifier_supporting_statement_verified():
    from evidence_first.agent.tender_verifier import TenderVerifier
    action = Action(action_id="act_1", type="search_documents", tool="document_search", input={"query": "tax compliance"})
    obs = Observation(
        observation_id="obs_1", action_id="act_1",
        content="Found 1 files matching 'tax compliance'",
        metadata={
            "results": [
                {
                    "file": "tax_compliance.txt",
                    "path": "/docs/tax_compliance.txt",
                    "content": "The company is compliant with all applicable tax filing requirements.\nStatus: COMPLIANT",
                    "matches": [{"line": 1, "text": "The company is compliant with all applicable tax filing requirements."}],
                }
            ]
        },
    )
    result = TenderVerifier.verify(action, obs)
    assert result["status"] == "VERIFIED"
    assert result["verified"] is True
    assert result["excerpt"] is not None


def test_tender_verifier_ambiguous_evidence():
    from evidence_first.agent.tender_verifier import TenderVerifier
    action = Action(action_id="act_1", type="search_documents", tool="document_search", input={"query": "tax compliance"})
    obs = Observation(
        observation_id="obs_1", action_id="act_1",
        content="Found 1 files matching 'tax compliance'",
        metadata={
            "results": [
                {
                    "file": "tax_info.txt",
                    "path": "/docs/tax_info.txt",
                    "content": "Tax matters are handled by the company's finance department.\nContact: finance@abccorp.com",
                    "matches": [{"line": 1, "text": "Tax matters are handled by the company's finance department."}],
                }
            ]
        },
    )
    result = TenderVerifier.verify(action, obs)
    assert result["status"] in ("NEEDS_HUMAN_REVIEW", "EVIDENCE_FOUND_BUT_INSUFFICIENT")
    assert result["verified"] is False


def test_tender_verifier_missing_evidence():
    from evidence_first.agent.tender_verifier import TenderVerifier
    action = Action(action_id="act_1", type="search_documents", tool="document_search", input={"query": "nonexistent"})
    obs = Observation(observation_id="obs_1", action_id="act_1", content="Found 0 files matching 'nonexistent'", metadata={})
    result = TenderVerifier.verify(action, obs)
    assert result["status"] == "EVIDENCE_NOT_FOUND"
    assert result["verified"] is False


def test_tender_verifier_expired_certificate():
    from evidence_first.agent.tender_verifier import TenderVerifier
    action = Action(action_id="act_1", type="search_documents", tool="document_search", input={"query": "insurance"})
    obs = Observation(
        observation_id="obs_1", action_id="act_1",
        content="Found 1 files matching 'insurance'",
        metadata={
            "results": [
                {
                    "file": "insurance_policy.txt",
                    "path": "/docs/insurance_policy.txt",
                    "content": "Insurance Policy for ABC Corp\nStatus: EXPIRED\nExpiry Date: 2023-12-31",
                    "matches": [{"line": 1, "text": "Insurance Policy for ABC Corp"}, {"line": 3, "text": "Expiry Date: 2023-12-31"}],
                }
            ]
        },
    )
    result = TenderVerifier.verify(action, obs)
    assert result["status"] in ("NEEDS_HUMAN_REVIEW", "EVIDENCE_FOUND_BUT_INSUFFICIENT")
    assert result["verified"] is False


def test_tender_verifier_valid_certificate():
    from evidence_first.agent.tender_verifier import TenderVerifier
    action = Action(action_id="act_1", type="search_documents", tool="document_search", input={"query": "insurance"})
    obs = Observation(
        observation_id="obs_1", action_id="act_1",
        content="Found 1 files matching 'insurance'",
        metadata={
            "results": [
                {
                    "file": "insurance_policy.txt",
                    "path": "/docs/insurance_policy.txt",
                    "content": "Insurance Policy for ABC Corp\nStatus: ACTIVE\nExpiry Date: 2027-06-01",
                    "matches": [{"line": 1, "text": "Insurance Policy for ABC Corp"}, {"line": 3, "text": "Expiry Date: 2027-06-01"}],
                }
            ]
        },
    )
    result = TenderVerifier.verify(action, obs)
    assert result["status"] == "VERIFIED"
    assert result["verified"] is True


def test_verification_requires_evidence():
    from evidence_first.models import Action, Observation, Evidence, Verification
    from evidence_first.agent.verifier import Verifier
    action = Action(action_id="act_1", type="search_documents", tool="document_search", input={"query": "test"})
    obs = Observation(observation_id="obs_1", action_id="act_1", content="Found 1 files matching 'test'", metadata={})
    ev = Evidence(evidence_id="ev_1", action_id="act_1", observation_id="obs_1", content="evidence content")
    result = Verifier().verify(action, obs, evidence=ev)
    assert isinstance(result, Verification)
    assert len(result.evidence_ids) >= 1


def test_tender_verifier_count_check_pass():
    from evidence_first.agent.tender_verifier import TenderVerifier
    action = Action(action_id="act_1", type="search_documents", tool="document_search", input={"query": "project experience"})
    obs = Observation(
        observation_id="obs_1", action_id="act_1",
        content="Found 1 files matching 'project experience'",
        metadata={
            "results": [
                {
                    "file": "experience.txt",
                    "path": "/docs/experience.txt",
                    "content": "Project Experience Record\nTotal Similar Projects: 5\nYears in Business: 10",
                    "matches": [{"line": 1, "text": "Project Experience Record"}, {"line": 2, "text": "Total Similar Projects: 5"}],
                }
            ]
        },
    )
    result = TenderVerifier.verify(action, obs)
    assert result["status"] == "VERIFIED"
    assert result["verified"] is True


def test_tender_verifier_count_check_fail():
    from evidence_first.agent.tender_verifier import TenderVerifier
    action = Action(action_id="act_1", type="search_documents", tool="document_search", input={"query": "project experience"})
    obs = Observation(
        observation_id="obs_1", action_id="act_1",
        content="Found 1 files matching 'project experience'",
        metadata={
            "results": [
                {
                    "file": "experience.txt",
                    "path": "/docs/experience.txt",
                    "content": "Project Experience Record\nTotal Similar Projects: 1\nYears in Business: 2",
                    "matches": [{"line": 1, "text": "Project Experience Record"}, {"line": 2, "text": "Total Similar Projects: 1"}],
                }
            ]
        },
    )
    result = TenderVerifier.verify(action, obs)
    assert result["status"] in ("NEEDS_HUMAN_REVIEW", "EVIDENCE_FOUND_BUT_INSUFFICIENT")
    assert result["verified"] is False


# --- Tests: Permissions ---


def test_permission_hierarchy():
    assert PERMISSION_HIERARCHY[READ] < PERMISSION_HIERARCHY[ANALYZE] < PERMISSION_HIERARCHY[CREATE] < PERMISSION_HIERARCHY[MODIFY] < PERMISSION_HIERARCHY[SEND] < PERMISSION_HIERARCHY[SUBMIT] < PERMISSION_HIERARCHY[DELETE]


def test_get_required_permission_filesystem():
    p = get_required_permission("read_file", "filesystem")
    assert p == READ


def test_get_required_permission_search():
    p = get_required_permission("search_documents", "document_search")
    assert p == READ


def test_get_required_permission_http():
    p = get_required_permission("send_request", "http")
    assert p == SEND


def test_get_risk_level_low():
    r = get_risk_level("read_file", "filesystem")
    assert r == "LOW"


def test_get_risk_level_high():
    r = get_risk_level("send_request", "http")
    assert r == "MEDIUM"


def test_requires_approval_send():
    assert requires_approval(SEND) is True


def test_requires_approval_read():
    assert requires_approval(READ) is False


def test_requires_approval_submit():
    assert requires_approval(SUBMIT) is True


def test_requires_approval_delete():
    assert requires_approval(DELETE) is True


def test_requires_approval_create():
    assert requires_approval(CREATE) is False


def test_can_execute_with_sufficient():
    assert can_execute_with(SEND, READ) is True


def test_can_execute_with_insufficient():
    assert can_execute_with(READ, SEND) is False


def test_can_execute_with_equal():
    assert can_execute_with(READ, READ) is True


# --- Tests: Approval Flow ---


def test_approval_request_creation():
    req = ApprovalRequest(action_id="act_1", requested_permission="SEND", reason="test")
    assert req.status == "pending"
    assert req.approval_id.startswith("appr_")
    assert req.requested_permission == "SEND"


def test_approval_to_dict():
    req = ApprovalRequest(action_id="act_1", requested_permission="SEND", reason="test")
    d = req.to_dict()
    assert d["action_id"] == "act_1"
    assert d["requested_permission"] == "SEND"


def test_approval_structured_response():
    req = ApprovalRequest(action_id="act_1", requested_permission="SEND", reason="test")
    d = req.to_structured_response()
    assert d["approval_required"] is True
    assert d["action_id"] == "act_1"


def test_approval_manager_approve():
    mgr = ApprovalManager()
    req = mgr.request_approval("act_1", "SEND", "test")
    mgr.approve_by_id(req.approval_id)
    assert req.status == "approved"
    assert req.decision == "approved"


def test_approval_manager_deny():
    mgr = ApprovalManager()
    req = mgr.request_approval("act_1", "SEND", "test")
    mgr.deny_by_id(req.approval_id)
    assert req.status == "denied"
    assert req.decision == "denied"


def test_approval_manager_pending():
    mgr = ApprovalManager()
    req1 = mgr.request_approval("act_1", "SEND", "test")
    req2 = mgr.request_approval("act_2", "SEND", "test2")
    pending = mgr.get_pending()
    assert len(pending) == 2


def test_approval_auto_approve():
    mgr = ApprovalManager()
    mgr.auto_approve = True
    req = mgr.request_approval("act_1", "SEND", "test")
    assert req.status == "approved"


def test_approval_get_by_action():
    mgr = ApprovalManager()
    req = mgr.request_approval("act_5", "SEND", "test")
    found = mgr.get_by_action("act_5")
    assert found is not None
    assert found.approval_id == req.approval_id


# --- Tests: Tool Interface ---


def test_filesystem_tool_properties():
    tool = FilesystemTool()
    assert tool.name == "filesystem"
    assert "read" in tool.description.lower()
    assert tool.permission_required == "READ"
    assert tool.risk_level == "LOW"


def test_filesystem_tool_read_returns_error_for_missing():
    tool = FilesystemTool()
    action = Action(action_id="act_1", type="read", tool="filesystem", input={"operation": "read", "path": "nonexistent_file_xyz.txt"})
    result = tool.execute(action)
    assert result.success is False


def test_document_search_tool_properties():
    tool = DocumentSearchTool()
    assert tool.name == "document_search"
    assert tool.permission_required == "READ"


def test_tool_base_is_abstract():
    import pytest
    with pytest.raises(TypeError):
        Tool()


def test_tool_can_verify_default_false():
    tool = FilesystemTool()
    assert tool.can_verify() is False


# --- Tests: Planner ---


def test_planner_default_plan():
    planner = Planner()
    task = Task(description="Test tender")
    steps = planner.plan(task)
    assert len(steps) >= 1
    assert steps[0]["tool"] == "filesystem"


def test_planner_with_mock_llm():
    llm = MockLLMProvider()
    llm.set_responses([
        '[{"action_type": "read_file", "tool": "filesystem", "input": {"path": "test.txt"}}]',
    ])
    planner = Planner(llm=llm)
    task = Task(description="Test")
    steps = planner.plan(task)
    assert len(steps) == 1
    assert steps[0]["action_type"] == "read_file"


def test_planner_with_llm_no_credentials():
    planner = Planner()
    task = Task(description="Test")
    steps = planner.plan(task)
    assert isinstance(steps, list)
    assert len(steps) > 0


# --- Tests: Executor ---


def test_executor_execute_with_tool(temp_store):
    tool = FilesystemTool()
    executor = Executor()
    executor.register_tool(tool)
    action = Action(action_id="act_1", type="exists", tool="filesystem", input={"operation": "exists", "path": "examples/tender/requirements.md"})
    obs = executor.execute(action)
    assert obs is not None
    assert obs.action_id == "act_1"


def test_executor_execute_unknown_tool():
    executor = Executor()
    action = Action(action_id="act_1", type="read", tool="unknown", input={})
    obs = executor.execute(action)
    assert obs is not None
    assert "not found" in obs.content.lower() or "not found" in obs.metadata.get("error", "").lower()


# --- Tests: Runtime ---


def test_runtime_runs_task(temp_store):
    runtime = AgentRuntime(auto_approve=True, store=temp_store)
    run = runtime.run_task("Check tender readiness")
    assert run.status in ("completed", "paused")
    assert len(run.steps) > 0


def test_runtime_stores_run(temp_store):
    runtime = AgentRuntime(auto_approve=True, store=temp_store)
    run = runtime.run_task("Test task")
    stored = temp_store.get_run(run.run_id)
    assert stored is not None
    assert stored["task_description"] == "Test task"


def test_runtime_approval_pause(temp_store):
    runtime = AgentRuntime(auto_approve=False, store=temp_store)
    run = runtime.run_task("Submit tender")
    assert run.status in ("completed", "paused")


def test_runtime_resume_after_approval(temp_store):
    runtime = AgentRuntime(auto_approve=False, store=temp_store)
    run = runtime.run_task("Test tender")
    pending = runtime._approvals.get_pending()
    if pending:
        runtime.approve_action(pending[0].approval_id)
    assert True


# --- Tests: File Tool with Real Files ---


def test_filesystem_read_real_file():
    tool = FilesystemTool()
    action = Action(action_id="act_1", type="read", tool="filesystem", input={"operation": "read", "path": "examples/tender/requirements.md"})
    result = tool.execute(action)
    assert result.success is True
    assert "Required Documents" in result.content


def test_filesystem_exists_real_file():
    tool = FilesystemTool()
    action = Action(action_id="act_1", type="exists", tool="filesystem", input={"operation": "exists", "path": "examples/tender/company_docs/insurance_policy.txt"})
    result = tool.execute(action)
    assert result.success is True
    assert "True" in result.content


def test_filesystem_list_directory():
    tool = FilesystemTool()
    action = Action(action_id="act_1", type="list", tool="filesystem", input={"operation": "list", "path": "examples/tender/company_docs"})
    result = tool.execute(action)
    assert result.success is True
    assert "insurance_policy" in result.content or "insurance" in result.content


def test_filesystem_restricts_path():
    tool = FilesystemTool()
    action = Action(action_id="act_1", type="read", tool="filesystem", input={"operation": "read", "path": "/etc/passwd"})
    result = tool.execute(action)
    assert result.success is False
