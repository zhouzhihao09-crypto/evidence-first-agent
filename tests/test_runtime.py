import os
import tempfile

import pytest

from evidence_first.evidence.store import EvidenceStore
from evidence_first.models import Run, Action, Observation, Evidence, Verification, Task
from evidence_first.runtime.run import AgentRuntime
from evidence_first.tools.filesystem import FilesystemTool
from evidence_first.tools.search import DocumentSearchTool
from evidence_first.llm.mock import MockLLMProvider
from evidence_first.agent.planner import Planner
from evidence_first.agent.executor import Executor
from evidence_first.agent.verifier import Verifier
from evidence_first.runtime.permissions import READ, SEND, SUBMIT, DELETE
from evidence_first.runtime.approvals import ApprovalManager


def make_runtime(store):
    return AgentRuntime(
        llm=MockLLMProvider(),
        tools=[FilesystemTool(), DocumentSearchTool()],
        auto_approve=True,
        store=store,
    )


def test_full_tender_workflow():
    tmpdir = tempfile.mkdtemp()
    try:
        store = EvidenceStore(db_path=os.path.join(tmpdir, "evidence.db"))
        try:
            runtime = make_runtime(store)
            run = runtime.run_task("Determine whether a company has the documents required for a tender")
            assert run.status in ("completed", "paused")
            assert len(run.steps) >= 1
            assert run.run_id is not None
        finally:
            store.close()
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_tender_readiness_report_generated():
    tmpdir = tempfile.mkdtemp()
    try:
        store = EvidenceStore(db_path=os.path.join(tmpdir, "evidence.db"))
        try:
            runtime = make_runtime(store)
            run = runtime.run_task("Test tender")
            assert run.results is not None
        finally:
            store.close()
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_run_has_steps_with_actions():
    tmpdir = tempfile.mkdtemp()
    try:
        store = EvidenceStore(db_path=os.path.join(tmpdir, "evidence.db"))
        try:
            runtime = make_runtime(store)
            run = runtime.run_task("Sample task")
            for step in run.steps:
                assert "action_id" in step or "step" in step
                assert "status" in step
        finally:
            store.close()
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_run_completion_updates_store():
    tmpdir = tempfile.mkdtemp()
    try:
        store = EvidenceStore(db_path=os.path.join(tmpdir, "evidence.db"))
        try:
            runtime = make_runtime(store)
            run = runtime.run_task("Test")
            stored = store.get_run(run.run_id)
            assert stored is not None
            assert stored["status"] in ("completed", "paused")
        finally:
            store.close()
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_actions_linked_to_run():
    tmpdir = tempfile.mkdtemp()
    try:
        store = EvidenceStore(db_path=os.path.join(tmpdir, "evidence.db"))
        try:
            runtime = make_runtime(store)
            run = runtime.run_task("Test actions")
            actions = store.get_actions(run.run_id)
            assert len(actions) >= 1
            for a in actions:
                assert a["run_id"] == run.run_id
        finally:
            store.close()
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_observations_linked_to_actions():
    tmpdir = tempfile.mkdtemp()
    try:
        store = EvidenceStore(db_path=os.path.join(tmpdir, "evidence.db"))
        try:
            runtime = make_runtime(store)
            run = runtime.run_task("Test observations")
            actions = store.get_actions(run.run_id)
            if actions:
                for a in actions:
                    break
                obs = store.get_observations(run.run_id)
                assert len(obs) >= 1
        finally:
            store.close()
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_evidence_has_content():
    tmpdir = tempfile.mkdtemp()
    try:
        store = EvidenceStore(db_path=os.path.join(tmpdir, "evidence.db"))
        try:
            runtime = make_runtime(store)
            run = runtime.run_task("Test evidence")
            evidence = store.get_evidence(run.run_id)
            if evidence:
                assert evidence[0]["content"] is not None
        finally:
            store.close()
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_verification_returns_structure():
    v = Verifier()
    action = Action(action_id="act_1", type="read", tool="filesystem", input={})
    obs = Observation(observation_id="obs_1", action_id="act_1", content="completed successfully", metadata={"success": True})
    ev = Evidence(evidence_id="ev_1", action_id="act_1", observation_id="obs_1", content="test")
    result = v.verify(action, obs, evidence=ev)
    assert isinstance(result, Verification)
    assert hasattr(result, "verified")
    assert hasattr(result, "confidence")
    assert hasattr(result, "reason")
    assert hasattr(result, "evidence_ids")


def test_graph_edges_created():
    tmpdir = tempfile.mkdtemp()
    try:
        store = EvidenceStore(db_path=os.path.join(tmpdir, "evidence.db"))
        try:
            runtime = make_runtime(store)
            run = runtime.run_task("Test graph")
            graph = store.get_graph_for_run(run.run_id)
            assert "nodes" in graph
            assert "edges" in graph
        finally:
            store.close()
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_run_has_approval_tracking():
    tmpdir = tempfile.mkdtemp()
    try:
        store = EvidenceStore(db_path=os.path.join(tmpdir, "evidence.db"))
        try:
            runtime = AgentRuntime(
                llm=MockLLMProvider(),
                tools=[FilesystemTool(), DocumentSearchTool()],
                auto_approve=False,
                store=store,
            )
            run = runtime.run_task("Important task")
            assert run.approval_requests is not None
            assert isinstance(run.approval_requests, list)
        finally:
            store.close()
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_deterministic_mock_execution():
    llm = MockLLMProvider()
    llm.set_responses(['[{"action_type": "read_file", "tool": "filesystem", "input": {"path": "test"}}]'])
    planner = Planner(llm=llm)
    task = Task(description="test")
    steps = planner.plan(task)
    assert len(steps) == 1
