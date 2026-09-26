import json
import os
import shutil
import tempfile

import pytest
from click.testing import CliRunner

from evidence_first.evidence.store import EvidenceStore
from evidence_first.receipt import (
    HASH_ALGORITHM,
    RECEIPT_VERSION,
    build_receipt,
    canonical_json,
    compute_receipt_hash,
    render_receipt_text,
    seal_receipt,
    verify_receipt_hash,
)
from evidence_first.runtime.run import AgentRuntime
from evidence_first.tools.filesystem import FilesystemTool
from evidence_first.tools.search import DocumentSearchTool
from evidence_first.work import WorkLedger


@pytest.fixture
def store():
    tmpdir = tempfile.mkdtemp()
    instance = EvidenceStore(db_path=os.path.join(tmpdir, "evidence.db"))
    try:
        yield instance
    finally:
        instance.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def make_ledger(store, workdir=None):
    return WorkLedger(store=store, workdir=workdir or os.getcwd())


def make_verified_run(store, task="Fix authentication bug", agent="simulated coding agent"):
    ledger = make_ledger(store)
    run = ledger.start_run(task=task, agent=agent, objective="Authentication tests pass")
    ledger.record_evidence(
        run.run_id,
        "run_tests",
        "Ran the authentication test suite",
        source="pytest",
        content="tests/test_auth.py::test_login PASSED\n12 passed in 0.42s",
    )
    claim = ledger.create_claim(
        run.run_id,
        "All authentication tests pass.",
        check={"type": "keyword_present", "keyword": "12 passed"},
        evidence_ids=[],
    )
    outcome = ledger.verify_claim(claim["claim_id"])
    return ledger, run, claim, outcome


# --- Structure --------------------------------------------------------------

def test_receipt_has_version_and_core_sections(store):
    ledger, run, _, _ = make_verified_run(store)
    receipt = build_receipt(store, run.run_id)

    assert receipt["version"] == RECEIPT_VERSION
    assert receipt["run_id"] == run.run_id
    for section in ("run", "actions", "evidence", "claims", "verifications", "approvals", "result", "integrity"):
        assert section in receipt
    assert receipt["run"]["agent"] == "simulated coding agent"
    assert receipt["run"]["objective"] == "Authentication tests pass"
    assert receipt["run"]["task"] == "Fix authentication bug"


def test_receipt_is_json_serializable(store):
    ledger, run, _, _ = make_verified_run(store)
    receipt = build_receipt(store, run.run_id)
    text = canonical_json(receipt)
    assert json.loads(text)["integrity"]["hash"] == receipt["integrity"]["hash"]


def test_receipt_for_unknown_run_is_none(store):
    assert build_receipt(store, "run_missing") is None


def test_receipt_captures_actions_evidence_claims_verifications(store):
    ledger, run, _, _ = make_verified_run(store)
    receipt = build_receipt(store, run.run_id)

    assert len(receipt["actions"]) >= 1
    assert len(receipt["evidence"]) >= 1
    assert len(receipt["claims"]) == 1
    assert receipt["claims"][0]["status"] == "VERIFIED"
    assert len(receipt["verifications"]) >= 1
    assert receipt["result"]["verified"] == 1
    assert receipt["result"]["status"] == "VERIFIED"


# --- Deterministic hashing (PHASE 3) ----------------------------------------

def test_same_receipt_produces_same_hash(store):
    ledger, run, _, _ = make_verified_run(store)
    first = build_receipt(store, run.run_id)
    second = build_receipt(store, run.run_id)
    assert first["integrity"]["hash"] == second["integrity"]["hash"]


def test_hash_is_deterministic_for_equal_payloads():
    payload = {"b": 2, "a": {"z": 1, "y": [3, 2, 1]}}
    reordered = {"a": {"y": [3, 2, 1], "z": 1}, "b": 2}
    assert compute_receipt_hash(payload) == compute_receipt_hash(reordered)


def test_hash_prefix_and_algorithm(store):
    ledger, run, _, _ = make_verified_run(store)
    receipt = build_receipt(store, run.run_id)
    assert receipt["integrity"]["algorithm"] == HASH_ALGORITHM
    assert receipt["integrity"]["hash"].startswith("sha256:")
    assert len(receipt["integrity"]["hash"]) == len("sha256:") + 64


def test_hash_does_not_include_integrity_field(store):
    ledger, run, _, _ = make_verified_run(store)
    receipt = build_receipt(store, run.run_id)
    resealed = seal_receipt(receipt)
    assert resealed["integrity"]["hash"] == receipt["integrity"]["hash"]
    assert verify_receipt_hash(receipt) is True


def test_tampered_receipt_fails_hash_check(store):
    ledger, run, _, _ = make_verified_run(store)
    receipt = build_receipt(store, run.run_id)
    receipt["claims"][0]["status"] = "FAILED"
    assert verify_receipt_hash(receipt) is False


def test_changed_evidence_changes_hash(store):
    ledger_a, run_a, _, _ = make_verified_run(store, task="Fix authentication bug")
    receipt_a = build_receipt(store, run_a.run_id)

    ledger_b = make_ledger(store)
    run_b = ledger_b.start_run(task="Fix authentication bug", agent="simulated coding agent")
    ledger_b.record_evidence(
        run_b.run_id,
        "run_tests",
        "Ran the authentication test suite",
        source="pytest",
        content="tests/test_auth.py::test_login FAILED\n1 failed in 0.42s",
    )
    receipt_b = build_receipt(store, run_b.run_id)

    assert receipt_a["integrity"]["hash"] != receipt_b["integrity"]["hash"]


def test_changed_verification_changes_hash(store):
    ledger_a, run_a, _, _ = make_verified_run(store)
    receipt_a = build_receipt(store, run_a.run_id)

    ledger_b = make_ledger(store)
    run_b = ledger_b.start_run(task="Fix authentication bug", agent="simulated coding agent")
    ledger_b.record_evidence(
        run_b.run_id, "run_tests", "Ran the authentication test suite", content="1 failed in 0.42s"
    )
    claim = ledger_b.create_claim(
        run_b.run_id, "All authentication tests pass.", check={"type": "keyword_present", "keyword": "12 passed"}
    )
    ledger_b.verify_claim(claim["claim_id"])
    receipt_b = build_receipt(store, run_b.run_id)

    assert receipt_a["result"]["status"] != receipt_b["result"]["status"]
    assert receipt_a["integrity"]["hash"] != receipt_b["integrity"]["hash"]


def test_changed_claim_changes_hash(store):
    ledger_a, run_a, _, _ = make_verified_run(store)
    receipt_a = build_receipt(store, run_a.run_id)

    ledger_b = make_ledger(store)
    run_b = ledger_b.start_run(task="Fix authentication bug", agent="simulated coding agent")
    ledger_b.record_evidence(
        run_b.run_id, "run_tests", "Ran the authentication test suite", content="12 passed in 0.42s"
    )
    claim = ledger_b.create_claim(
        run_b.run_id, "All authentication tests pass, including MFA.", check={"type": "keyword_present", "keyword": "12 passed"}
    )
    ledger_b.verify_claim(claim["claim_id"])
    receipt_b = build_receipt(store, run_b.run_id)

    assert receipt_a["integrity"]["hash"] != receipt_b["integrity"]["hash"]


def test_canonical_json_is_compact_and_sorted():
    text = canonical_json({"b": 1, "a": 2})
    assert text == '{"a":2,"b":1}'


# --- Result states ----------------------------------------------------------

def test_result_needs_review_when_evidence_insufficient(store):
    ledger = make_ledger(store)
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    claim = ledger.create_claim(
        run.run_id, "All authentication tests pass.", check={"type": "evidence_count", "min_count": 1}
    )
    ledger.verify_claim(claim["claim_id"])
    receipt = build_receipt(store, run.run_id)

    assert receipt["claims"][0]["status"] == "INSUFFICIENT_EVIDENCE"
    assert receipt["result"]["status"] == "NEEDS_REVIEW"
    assert receipt["result"]["insufficient_evidence"] == 1


def test_claim_without_check_is_never_auto_verified(store):
    ledger = make_ledger(store)
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    ledger.record_evidence(run.run_id, "run_tests", "Ran tests", content="12 passed")
    claim = ledger.create_claim(run.run_id, "All authentication tests pass.")
    outcome = ledger.verify_claim(claim["claim_id"])

    assert outcome["status"] == "NOT_VERIFIED"
    assert "check" in outcome["reason"].lower()


def test_result_failed_when_a_claim_fails(store):
    ledger = make_ledger(store)
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    ledger.record_evidence(run.run_id, "run_tests", "Ran tests", content="1 failed in 0.1s")
    claim = ledger.create_claim(
        run.run_id, "All authentication tests pass.", check={"type": "keyword_present", "keyword": "12 passed"}
    )
    ledger.verify_claim(claim["claim_id"])
    receipt = build_receipt(store, run.run_id)

    assert receipt["claims"][0]["status"] == "FAILED"
    assert receipt["result"]["status"] == "FAILED"


def test_result_not_verified_for_run_without_claims(store):
    ledger = make_ledger(store)
    run = ledger.start_run(task="Do something", agent="agent")
    ledger.record_evidence(run.run_id, "inspect", "Looked at a file", content="contents")
    receipt = build_receipt(store, run.run_id)

    assert receipt["result"]["status"] == "NEEDS_REVIEW"
    assert receipt["result"]["claims_total"] == 0


# --- Secrets ----------------------------------------------------------------

def test_receipt_never_contains_recorded_secrets(store):
    ledger = make_ledger(store)
    run = ledger.start_run(task="Deploy", agent="agent")
    ledger.record_evidence(
        run.run_id,
        "http",
        "Called the API",
        content="HTTP 200 OK\nAuthorization: Bearer sk-abcdefghijklmnopqrstuvwxyz123456\napi_key=sk-abcdefghijklmnopqrstuvwxyz123456",
    )
    receipt = build_receipt(store, run.run_id)
    text = canonical_json(receipt)

    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in text
    assert "REDACTED" in text


def test_redaction_also_applies_to_stored_evidence(store):
    ledger = make_ledger(store)
    run = ledger.start_run(task="Deploy", agent="agent")
    recorded = ledger.record_evidence(
        run.run_id, "http", "Called the API", content="password=hunter2secretvalue"
    )
    stored = store.get_evidence(run.run_id)[0]
    assert "hunter2secretvalue" not in stored["content"]
    assert recorded["evidence_id"] == stored["evidence_id"]


# --- Rendering --------------------------------------------------------------

def test_render_receipt_text_contains_sections(store):
    ledger, run, _, _ = make_verified_run(store)
    receipt = build_receipt(store, run.run_id)
    text = render_receipt_text(receipt)

    for heading in ("AI WORK RECEIPT", "ACTIONS", "EVIDENCE", "CLAIMS", "VERIFICATIONS", "APPROVALS", "RESULT", "RECEIPT HASH"):
        assert heading in text
    assert "VERIFIED" in text
    assert receipt["integrity"]["hash"] in text


# --- CLI --------------------------------------------------------------------

def test_cli_receipt_command_outputs_receipt(store):
    ledger, run, _, _ = make_verified_run(store)
    from evidence_first.cli.main import main as cli_main
    from unittest import mock

    runner = CliRunner()
    with mock.patch("evidence_first.cli.main.EvidenceStore", lambda db_path=None: store):
        result = runner.invoke(cli_main, ["receipt", run.run_id, "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["run_id"] == run.run_id
    assert payload["integrity"]["hash"].startswith("sha256:")


def test_cli_receipt_command_human_output(store):
    ledger, run, _, _ = make_verified_run(store)
    from evidence_first.cli.main import main as cli_main
    from unittest import mock

    runner = CliRunner()
    with mock.patch("evidence_first.cli.main.EvidenceStore", lambda db_path=None: store):
        result = runner.invoke(cli_main, ["receipt", run.run_id])
    assert result.exit_code == 0
    assert "AI Work Receipt" in result.output
    assert "VERIFIED" in result.output


def test_cli_receipt_command_unknown_run_fails(store):
    from evidence_first.cli.main import main as cli_main
    from unittest import mock

    runner = CliRunner()
    with mock.patch("evidence_first.cli.main.EvidenceStore", lambda db_path=None: store):
        result = runner.invoke(cli_main, ["receipt", "run_nope"])
    assert result.exit_code != 0


def test_evidence_first_group_exposes_receipt():
    from evidence_first.cli.main import evidence_first_main

    assert "receipt" in evidence_first_main.commands
    assert "inspect" in evidence_first_main.commands


# --- Legacy (tender) runs keep their truthful statuses -----------------------

def test_tender_run_receipt_keeps_evidence_status_truthful(store):
    runtime = AgentRuntime(
        tools=[FilesystemTool(), DocumentSearchTool()],
        auto_approve=True,
        store=store,
    )
    run = runtime.run_task("Determine whether a company has the documents required for a tender")
    receipt = build_receipt(store, run.run_id)

    assert receipt["result"]["claims_total"] == 11
    assert receipt["result"]["verified"] == 10
    assert receipt["result"]["insufficient_evidence"] == 1
    assert receipt["result"]["status"] == "NEEDS_REVIEW"

    insurance = [c for c in receipt["claims"] if "insurance" in c["statement"]]
    assert insurance, "insurance claim missing from derived claims"
    assert insurance[0]["status"] == "INSUFFICIENT_EVIDENCE"
    assert insurance[0]["engine_status"] == "EVIDENCE_FOUND_BUT_INSUFFICIENT"
