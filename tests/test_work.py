import hashlib
import os
import shutil
import tempfile

import pytest

from evidence_first.evidence.store import EvidenceStore
from evidence_first.verification import (
    FAILED,
    INSUFFICIENT_EVIDENCE,
    NOT_VERIFIED,
    VERIFIED,
    available_checks,
    to_claim_state,
)
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


@pytest.fixture
def ledger(store):
    return WorkLedger(store=store, workdir=os.getcwd())


# --- WorkRun ----------------------------------------------------------------

def test_start_run_records_agent_and_objective(ledger):
    run = ledger.start_run(task="Fix auth bug", agent="coding agent", objective="Tests pass")
    assert run.run_id.startswith("run_")
    assert run.status == "running"
    assert run.results["agent"] == "coding agent"
    assert run.results["objective"] == "Tests pass"
    assert ledger.get_run(run.run_id) is not None


def test_start_run_on_missing_store_entry_returns_none(ledger):
    assert ledger.get_run("run_nope") is None


# --- Actions / evidence -----------------------------------------------------

def test_record_evidence_creates_action_observation_and_evidence(ledger, store):
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    recorded = ledger.record_evidence(
        run.run_id, "run_tests", "Ran tests", source="pytest", content="12 passed"
    )

    assert recorded["evidence_id"].startswith("ev_")
    actions = store.get_actions(run.run_id)
    observations = store.get_observations(run.run_id)
    evidence = store.get_evidence(run.run_id)

    assert len(actions) == 1
    assert actions[0]["type"] == "run_tests"
    assert len(observations) == 1
    assert observations[0]["action_id"] == actions[0]["action_id"]
    assert len(evidence) == 1
    assert evidence[0]["action_id"] == actions[0]["action_id"]


def test_record_evidence_links_graph_edges(ledger, store):
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    recorded = ledger.record_evidence(run.run_id, "run_tests", "Ran tests", content="12 passed")
    edges = store.get_edges(source_id=recorded["action_id"])
    assert any(e["relationship"] == "produces" for e in edges)
    assert any(e["relationship"] == "supports" for e in store.get_edges(source_id=recorded["observation_id"]))


def test_record_evidence_on_missing_run_errors(ledger):
    assert "error" in ledger.record_evidence("run_nope", "run_tests", "Ran tests")


# --- Claims -----------------------------------------------------------------

def test_create_claim_defaults_to_not_verified(ledger):
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    claim = ledger.create_claim(run.run_id, "All authentication tests pass.")
    assert claim["status"] == NOT_VERIFIED
    assert claim["claim_id"].startswith("claim_")


def test_create_claim_rejects_unknown_check(ledger):
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    result = ledger.create_claim(run.run_id, "claim", check={"type": "vibes"})
    assert "Unknown check type" in result["error"]


def test_create_claim_rejects_empty_statement(ledger):
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    assert "error" in ledger.create_claim(run.run_id, "   ")


def test_create_claim_on_missing_run_errors(ledger):
    assert "error" in ledger.create_claim("run_nope", "claim")


def test_verify_keyword_present_is_verified(ledger):
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    ledger.record_evidence(run.run_id, "run_tests", "Ran tests", content="12 passed in 0.4s")
    claim = ledger.create_claim(
        run.run_id, "All authentication tests pass.", check={"type": "keyword_present", "keyword": "12 passed"}
    )
    outcome = ledger.verify_claim(claim["claim_id"])

    assert outcome["status"] == VERIFIED
    assert outcome["verification_id"].startswith("ver_")
    assert outcome["evidence_ids"]


def test_verify_keyword_absent_detects_findings(ledger):
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    ledger.record_evidence(run.run_id, "security_scan", "Ran bandit", content="HIGH: B105 hardcoded_password")
    claim = ledger.create_claim(
        run.run_id, "Security scan reports no high or critical findings.",
        check={"type": "keyword_absent", "keyword": "HIGH"},
    )
    assert ledger.verify_claim(claim["claim_id"])["status"] == FAILED


def test_verify_keyword_absent_passes_on_clean_scan(ledger):
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    ledger.record_evidence(run.run_id, "security_scan", "Ran bandit", content="No issues identified.")
    claim = ledger.create_claim(
        run.run_id, "Security scan reports no high or critical findings.",
        check={"type": "keyword_absent", "keyword": "HIGH"},
    )
    assert ledger.verify_claim(claim["claim_id"])["status"] == VERIFIED


def test_verify_without_evidence_is_insufficient(ledger):
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    claim = ledger.create_claim(
        run.run_id, "All authentication tests pass.", check={"type": "keyword_present", "keyword": "passed"}
    )
    outcome = ledger.verify_claim(claim["claim_id"])
    assert outcome["status"] == INSUFFICIENT_EVIDENCE
    assert outcome["evidence_considered"] == 0


def test_verify_claim_with_missing_id_errors(ledger):
    assert "error" in ledger.verify_claim("claim_nope")


def test_verify_persists_result_for_receipt(ledger, store):
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    ledger.record_evidence(run.run_id, "run_tests", "Ran tests", content="12 passed")
    claim = ledger.create_claim(
        run.run_id, "All authentication tests pass.", check={"type": "keyword_present", "keyword": "12 passed"}
    )
    ledger.verify_claim(claim["claim_id"])

    stored = store.get_claim(claim["claim_id"])
    assert stored["status"] == VERIFIED
    assert store.get_claim_verifications(run.run_id)


def test_verify_uses_only_linked_evidence_when_provided(ledger):
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    first = ledger.record_evidence(run.run_id, "run_tests", "old run", content="1 failed")
    ledger.record_evidence(run.run_id, "run_tests", "new run", content="12 passed")
    claim = ledger.create_claim(
        run.run_id,
        "All authentication tests pass.",
        check={"type": "keyword_present", "keyword": "12 passed"},
        evidence_ids=[first["evidence_id"]],
    )
    outcome = ledger.verify_claim(claim["claim_id"])
    assert outcome["status"] == FAILED
    assert outcome["evidence_linked"] is True
    assert outcome["evidence_considered"] == 1


# --- Deterministic checks ---------------------------------------------------

def test_file_exists_check(tmp_path, store):
    workdir = tempfile.mkdtemp()
    target = os.path.join(workdir, "auth.py")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("print('auth')\n")
    ledger = WorkLedger(store=store, workdir=workdir)
    try:
        run = ledger.start_run(task="Fix auth bug", agent="agent")
        claim = ledger.create_claim(
            run.run_id, "The required file exists.", check={"type": "file_exists", "path": "auth.py"}
        )
        assert ledger.verify_claim(claim["claim_id"])["status"] == VERIFIED

        missing = ledger.create_claim(
            run.run_id, "The missing file exists.", check={"type": "file_exists", "path": "nope.py"}
        )
        assert ledger.verify_claim(missing["claim_id"])["status"] == FAILED
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_file_exists_check_cannot_escape_working_directory(store):
    ledger = WorkLedger(store=store, workdir=os.getcwd())
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    claim = ledger.create_claim(
        run.run_id,
        "A file outside the workspace exists.",
        check={"type": "file_exists", "path": os.path.abspath(os.sep.join(["..", "..", "etc", "hosts"]))},
    )
    outcome = ledger.verify_claim(claim["claim_id"])
    assert outcome["status"] == FAILED
    assert "outside allowed working directory" in outcome["reason"]


def test_sha256_match_check(tmp_path, store):
    workdir = tempfile.mkdtemp()
    target = os.path.join(workdir, "auth.py")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("print('auth')\n")
    digest = hashlib.sha256(open(target, "rb").read()).hexdigest()
    ledger = WorkLedger(store=store, workdir=workdir)
    try:
        run = ledger.start_run(task="Fix auth bug", agent="agent")
        good = ledger.create_claim(
            run.run_id, "File content unchanged.", check={"type": "sha256_match", "path": "auth.py", "sha256": digest}
        )
        assert ledger.verify_claim(good["claim_id"])["status"] == VERIFIED

        bad = ledger.create_claim(
            run.run_id, "File content unchanged.", check={"type": "sha256_match", "path": "auth.py", "sha256": "0" * 64}
        )
        assert ledger.verify_claim(bad["claim_id"])["status"] == FAILED
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_verification_status_equals_uses_existing_engine_results(ledger):
    from evidence_first.models import Action, Observation
    from evidence_first.agent.verifier import Verifier
    from evidence_first.evidence.recorder import record_action, record_observation, record_verification

    run = ledger.start_run(task="Tender readiness", agent="agent")
    action = Action(action_id="act_x", type="search_documents", tool="document_search", input={"query": "insurance"})
    record_action(ledger.store, run.run_id, action)
    observation = Observation(
        observation_id="obs_x",
        action_id=action.action_id,
        content="Found 1 files matching 'insurance'",
        metadata={"success": True, "results": [{"content": "Insurance Policy\nExpiry Date: 2024-12-31"}]},
    )
    record_observation(ledger.store, observation)
    verification = Verifier().verify(action, observation, evidence=None, tool=None)
    record_verification(ledger.store, verification)

    claim = ledger.create_claim(
        run.run_id,
        "Insurance requirement is satisfied.",
        check={
            "type": "verification_status_equals",
            "status": "VERIFIED",
            "query_contains": "action completed successfully",
        },
    )
    assert ledger.verify_claim(claim["claim_id"])["status"] == VERIFIED


def test_verification_status_equals_fails_when_engine_did_not_verify(ledger):
    from evidence_first.models import Action, Observation
    from evidence_first.agent.verifier import Verifier
    from evidence_first.evidence.recorder import record_action, record_observation, record_verification

    run = ledger.start_run(task="Tender readiness", agent="agent")
    action = Action(action_id="act_y", type="search_documents", tool="document_search", input={"query": "insurance"})
    record_action(ledger.store, run.run_id, action)
    observation = Observation(
        observation_id="obs_y",
        action_id=action.action_id,
        content="",
        metadata={"success": False},
    )
    record_observation(ledger.store, observation)
    record_verification(ledger.store, Verifier().verify(action, observation))

    claim = ledger.create_claim(
        run.run_id,
        "Insurance requirement is satisfied.",
        check={"type": "verification_status_equals", "status": "VERIFIED"},
    )
    assert ledger.verify_claim(claim["claim_id"])["status"] == FAILED


def test_unknown_check_type_in_stored_claim_is_not_verified(ledger, store):
    from evidence_first.models import Claim

    run = ledger.start_run(task="Fix auth bug", agent="agent")
    claim = ledger.create_claim(run.run_id, "A claim.")
    stored = store.get_claim(claim["claim_id"])
    stored["check"] = {"type": "does_not_exist"}
    store.save_claim(Claim(
        claim_id=stored["claim_id"],
        run_id=stored["run_id"],
        statement=stored["statement"],
        status=stored["status"],
        evidence_ids=stored["evidence_ids"],
        verification_id=stored["verification_id"],
        check=stored["check"],
        created_at=stored["created_at"],
    ))
    assert ledger.verify_claim(claim["claim_id"])["status"] == NOT_VERIFIED


# --- Approvals --------------------------------------------------------------

def test_low_risk_action_needs_no_approval(ledger):
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    outcome = ledger.request_approval(run.run_id, "write_local_note", "note the change", permission="CREATE")
    assert outcome["approval_required"] is False
    assert outcome["status"] == "recorded"
    assert outcome["requested_permission"] == "CREATE"


def test_unclassified_action_fails_closed(ledger):
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    outcome = ledger.request_approval(run.run_id, "do_something_unusual", "no idea what this maps to")
    assert outcome["approval_required"] is True
    assert outcome["blocked"] is True
    assert outcome["requested_permission"] == "UNKNOWN_PERMISSION"


def test_consequential_action_is_blocked_pending_approval(ledger, store):
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    outcome = ledger.request_approval(run.run_id, "send_email", "Email the on-call engineer")

    assert outcome["approval_required"] is True
    assert outcome["status"] == "pending"
    assert outcome["blocked"] is True
    assert outcome["requested_permission"] == "SEND"

    pending = store.get_pending_approvals(run.run_id)
    assert len(pending) == 1
    assert pending[0]["requested_permission"] == "SEND"

    action = [a for a in store.get_actions(run.run_id) if a["action_id"] == outcome["action_id"]][0]
    assert action["status"] == "approval_pending"


def test_explicit_high_permission_action_is_blocked(ledger):
    run = ledger.start_run(task="Submit tender", agent="agent")
    outcome = ledger.request_approval(run.run_id, "publish", "Publish the tender", permission="SUBMIT")
    assert outcome["approval_required"] is True
    assert outcome["requested_permission"] == "SUBMIT"


def test_declared_permission_cannot_lower_a_consequential_action(ledger):
    """An agent must not clear a gate by declaring a lower permission."""
    run = ledger.start_run(task="Notify the team", agent="agent")
    outcome = ledger.request_approval(run.run_id, "send_email", "Email the release manager", permission="READ")
    assert outcome["approval_required"] is True
    assert outcome["requested_permission"] == "SEND"
    assert outcome["blocked"] is True


def test_declared_permission_cannot_lower_a_delete_action(ledger):
    run = ledger.start_run(task="Purge records", agent="agent")
    outcome = ledger.request_approval(run.run_id, "delete_records", "Purge customer rows", permission="CREATE")
    assert outcome["approval_required"] is True
    assert outcome["requested_permission"] == "DELETE"


def test_declared_permission_may_raise_the_bar(ledger):
    run = ledger.start_run(task="Touch a shared file", agent="agent")
    outcome = ledger.request_approval(
        run.run_id, "update_external", "Update the shared record", permission="DELETE"
    )
    assert outcome["approval_required"] is True
    assert outcome["requested_permission"] == "DELETE"


def test_declared_permission_classifies_an_unrecognised_action(ledger):
    run = ledger.start_run(task="Local note", agent="agent")
    outcome = ledger.request_approval(run.run_id, "write_local_note", "note the change", permission="CREATE")
    assert outcome["approval_required"] is False
    assert outcome["requested_permission"] == "CREATE"


def test_unknown_permission_value_is_rejected(ledger):
    """A nonsense permission must not be treated as a low-risk level."""
    run = ledger.start_run(task="Do something", agent="agent")
    outcome = ledger.request_approval(run.run_id, "do_thing", "reason", permission="SUPER")
    assert "error" in outcome
    assert "Unknown permission" in outcome["error"]
    assert ledger.store.get_approvals(run.run_id) == []


def test_empty_permission_value_falls_back_to_fail_closed(ledger):
    run = ledger.start_run(task="Do something", agent="agent")
    outcome = ledger.request_approval(run.run_id, "do_thing", "reason", permission="")
    assert outcome["approval_required"] is True
    assert outcome["requested_permission"] == "UNKNOWN_PERMISSION"


def test_request_approval_on_missing_run_errors(ledger):
    assert "error" in ledger.request_approval("run_nope", "send_email", "why")


# --- Completion -------------------------------------------------------------

def test_finish_run_finalizes_receipt(ledger):
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    ledger.record_evidence(run.run_id, "run_tests", "Ran tests", content="12 passed")
    claim = ledger.create_claim(
        run.run_id, "All authentication tests pass.", check={"type": "keyword_present", "keyword": "12 passed"}
    )
    ledger.verify_claim(claim["claim_id"])

    outcome = ledger.finish_run(run.run_id)
    assert outcome["status"] == "completed"
    assert outcome["result"] == "VERIFIED"
    assert outcome["receipt_hash"].startswith("sha256:")


def test_finish_run_does_not_auto_verify_claims(ledger):
    """Finishing must not verify claims on the agent's behalf.

    A claim that carries a check and would pass still stays NOT_VERIFIED until
    verify_claim is called, so an agent cannot reach a VERIFIED receipt by
    forgetting a step.
    """
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    ledger.record_evidence(run.run_id, "run_tests", "Ran tests", content="12 passed")
    claim = ledger.create_claim(
        run.run_id, "All authentication tests pass.", check={"type": "keyword_present", "keyword": "12 passed"}
    )

    outcome = ledger.finish_run(run.run_id)

    assert outcome["result"] == "NEEDS_REVIEW"
    assert ledger.store.get_claim(claim["claim_id"])["status"] == "NOT_VERIFIED"


def test_finished_receipt_hash_is_stable(ledger):
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    ledger.record_evidence(run.run_id, "run_tests", "Ran tests", content="12 passed")
    first = ledger.finish_run(run.run_id)
    second = ledger.finish_run(run.run_id)
    assert first["receipt_hash"] == second["receipt_hash"]


def test_finish_run_with_pending_approval_needs_review(ledger):
    run = ledger.start_run(task="Deploy", agent="agent")
    ledger.request_approval(run.run_id, "send_email", "Notify the team")
    outcome = ledger.finish_run(run.run_id)

    assert outcome["result"] == "NEEDS_REVIEW"
    assert outcome["receipt"]["result"]["pending_approvals"] == 1
    assert outcome["receipt"]["approvals"][0]["status"] == "pending"


def test_finish_missing_run_errors(ledger):
    assert "error" in ledger.finish_run("run_nope")


# --- Audit trail ------------------------------------------------------------

def test_audit_trail_contains_full_chain(ledger):
    run = ledger.start_run(task="Fix auth bug", agent="agent")
    ledger.record_evidence(run.run_id, "run_tests", "Ran tests", content="12 passed")
    claim = ledger.create_claim(
        run.run_id, "All authentication tests pass.", check={"type": "keyword_present", "keyword": "12 passed"}
    )
    ledger.verify_claim(claim["claim_id"])
    ledger.finish_run(run.run_id)

    trail = ledger.get_audit_trail(run.run_id)
    kinds = [event["event"] for event in trail["events"]]
    assert kinds[0] == "run_started"
    for expected in ("action", "observation", "evidence", "claim", "claim_verification"):
        assert expected in kinds
    assert [e["timestamp"] for e in trail["events"]] == sorted(e["timestamp"] for e in trail["events"])


def test_audit_trail_for_missing_run_is_none(ledger):
    assert ledger.get_audit_trail("run_nope") is None


# --- Status mapping ---------------------------------------------------------

def test_legacy_status_map_is_lossless():
    assert to_claim_state("VERIFIED") == VERIFIED
    assert to_claim_state("EVIDENCE_FOUND_BUT_INSUFFICIENT") == INSUFFICIENT_EVIDENCE
    assert to_claim_state("EVIDENCE_NOT_FOUND") == INSUFFICIENT_EVIDENCE
    assert to_claim_state("NEEDS_HUMAN_REVIEW") == NOT_VERIFIED
    assert to_claim_state("SOMETHING_NEW", verified=True) == VERIFIED
    assert to_claim_state(None) == NOT_VERIFIED


def test_available_checks_are_documented():
    checks = available_checks()
    for expected in ("evidence_count", "keyword_present", "keyword_absent", "file_exists", "sha256_match", "verification_status_equals"):
        assert expected in checks
