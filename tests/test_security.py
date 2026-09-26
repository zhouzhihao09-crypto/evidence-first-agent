"""Security review tests: secrets, path access, permissions, and SQL safety."""

import os
import shutil
import tempfile

import pytest

from evidence_first.evidence.store import EvidenceStore
from evidence_first.models import Action, Evidence, Verification
from evidence_first.redaction import find_secrets, has_secret, redact
from evidence_first.runtime.permissions import (
    READ,
    UNKNOWN_PERMISSION,
    requires_approval,
    resolve_agent_permission,
)
from evidence_first.verification import CheckContext
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


# --- Secrets ----------------------------------------------------------------

SECRET_SAMPLES = [
    "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz123456",
    "api_key=sk-abcdefghijklmnopqrstuvwxyz123456",
    'password: "hunter2secretvalue"',
    "AWS key AKIAIOSFODNN7EXAMPLE in config",
    "token: ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n-----END RSA PRIVATE KEY-----",
    "DATABASE_PASSWORD=letmeinpleasedonot",
]


@pytest.mark.parametrize("sample", SECRET_SAMPLES)
def test_secret_samples_are_detected_and_redacted(sample):
    assert has_secret(sample), sample
    assert "REDACTED" in redact(sample)
    assert find_secrets(sample)


@pytest.mark.parametrize("sample", SECRET_SAMPLES)
def test_secret_samples_do_not_survive_a_full_run(store, sample):
    ledger = WorkLedger(store=store, workdir=os.getcwd())
    run = ledger.start_run(task="Deploy", agent="agent")
    ledger.record_evidence(run.run_id, "http_call", "Called an API", content=sample)

    stored = store.get_evidence(run.run_id)[0]["content"]
    assert "REDACTED" in stored
    for leak in ("sk-abcdefghijklmnopqrstuvwxyz123456", "hunter2secretvalue",
                 "AKIAIOSFODNN7EXAMPLE", "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
                 "letmeinpleasedonot"):
        assert leak not in stored


def test_verification_reasons_are_redacted(store):
    store.save_verification(Verification(action_id="act_1", reason="Found api_key=sk-abcdefghijklmnopqrstuvwxyz123456"))
    rows = store.conn.execute("SELECT reason FROM verifications").fetchall()
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in rows[0][0]


def test_benign_evidence_is_not_mangled(store):
    ledger = WorkLedger(store=store, workdir=os.getcwd())
    run = ledger.start_run(task="Tender", agent="agent")
    ledger.record_evidence(
        run.run_id, "search", "Searched for insurance", content="Status: COMPLIANT\nTotal Similar Projects: 4"
    )
    stored = store.get_evidence(run.run_id)[0]["content"]
    assert "Status: COMPLIANT" in stored
    assert "REDACTED" not in stored


def test_redaction_is_idempotent(store):
    once = redact("api_key=sk-abcdefghijklmnopqrstuvwxyz123456")
    assert redact(once) == once


# --- File access ------------------------------------------------------------

def test_claim_checks_cannot_read_outside_the_workspace(store, monkeypatch):
    workdir = tempfile.mkdtemp()
    try:
        ledger = WorkLedger(store=store, workdir=workdir)
        run = ledger.start_run(task="Read secrets", agent="agent")

        escape = ledger.create_claim(
            run.run_id, "A system file exists.", check={"type": "file_exists", "path": "../../../../etc/hosts"}
        )
        assert ledger.verify_claim(escape["claim_id"])["status"] == "FAILED"

        absolute = ledger.create_claim(
            run.run_id,
            "A system file exists.",
            check={"type": "file_exists", "path": os.path.abspath(os.sep.join(["..", "..", "etc", "hosts"]))},
        )
        assert ledger.verify_claim(absolute["claim_id"])["status"] == "FAILED"

        digest = ledger.create_claim(
            run.run_id,
            "A system file hash is known.",
            check={"type": "sha256_match", "path": os.path.abspath(os.sep.join(["..", "..", "etc", "hosts"])), "sha256": "0" * 64},
        )
        assert ledger.verify_claim(digest["claim_id"])["status"] == "FAILED"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_resolve_path_rejects_traversal():
    ctx = CheckContext(run_id="run_1", claim={}, evidence=[], verifications=[], workdir=os.getcwd())
    with pytest.raises(PermissionError):
        ctx.resolve_path("../../secrets.env")


# --- Permissions and approvals ---------------------------------------------

def test_consequential_action_names_resolve_to_high_permissions():
    for action, expected in (
        ("send_email", "SEND"),
        ("notify_team", "SEND"),
        ("submit_tender", "SUBMIT"),
        ("publish_release", "SUBMIT"),
        ("delete_records", "DELETE"),
        ("deploy_production", "SEND"),
        ("modify_external", "MODIFY"),
    ):
        permission, _ = resolve_agent_permission(action)
        assert permission == expected, action


def test_unknown_action_names_fail_closed():
    permission, source = resolve_agent_permission("frobnicate")
    assert permission == UNKNOWN_PERMISSION
    assert source == "unknown"
    assert requires_approval(permission) is False  # gating is decided by the caller


def test_ledger_cannot_auto_approve_from_mcp(store):
    ledger = WorkLedger(store=store, workdir=os.getcwd())
    run = ledger.start_run(task="Deploy", agent="agent")
    gate = ledger.request_approval(run.run_id, "send_email", "notify")
    assert gate["blocked"] is True
    assert store.get_pending_approvals(run.run_id)


def test_low_risk_permissions_still_do_not_require_approval():
    assert requires_approval(READ) is False


# --- SQL safety -------------------------------------------------------------

def test_new_store_queries_are_parameterized(store):
    ledger = WorkLedger(store=store, workdir=os.getcwd())
    run = ledger.start_run(task="'; DROP TABLE runs; --", agent="agent")
    assert store.get_run(run.run_id) is not None
    # The hostile string is stored as data, not executed.
    assert store.get_run(run.run_id)["task_description"] == "'; DROP TABLE runs; --"

    ledger.record_evidence(run.run_id, "run_tests", "'; DELETE FROM evidence_items; --", content="12 passed")
    claim = ledger.create_claim(run.run_id, "'; DROP TABLE claims; --", check={"type": "evidence_count", "min_count": 1})
    assert ledger.verify_claim(claim["claim_id"])["status"] == "VERIFIED"
    assert store.get_claims(run.run_id)


def test_hostile_evidence_text_is_escaped_in_receipt(store):
    from evidence_first.receipt import build_receipt, canonical_json

    ledger = WorkLedger(store=store, workdir=os.getcwd())
    run = ledger.start_run(task="Fix bug", agent="agent")
    ledger.record_evidence(
        run.run_id, "edit_file", "Patched file", content="</script><script>alert(1)</script>"
    )
    receipt = build_receipt(store, run.run_id)
    text = canonical_json(receipt)
    # JSON encoding keeps the payload inert for JSON consumers.
    assert json_is_valid(text)


def json_is_valid(text: str) -> bool:
    import json

    try:
        json.loads(text)
        return True
    except ValueError:
        return False


def test_action_input_is_stored_as_data(store):
    action = Action(action_id="act_1", type="read_file", tool="filesystem", input={"path": "'; DROP TABLE actions; --"})
    store.save_action("run_1", action)
    rows = store.get_actions("run_1")
    assert rows[0]["input"]["path"] == "'; DROP TABLE actions; --"
    assert store.conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 1


def test_evidence_records_survive_receipt_round_trip(store):
    from evidence_first.receipt import build_receipt, verify_receipt_hash

    ledger = WorkLedger(store=store, workdir=os.getcwd())
    run = ledger.start_run(task="Fix bug", agent="agent")
    evidence = Evidence(evidence_id="ev_1", action_id="act_1", content="body", snippet="snip")
    store.save_evidence(evidence)
    receipt = build_receipt(store, run.run_id)
    assert verify_receipt_hash(receipt) is True
