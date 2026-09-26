"""MCP integration tests.

Uses the official SDK's in-memory transport (``mcp.Client(server)``): no
subprocesses, no ports, no network. Every test drives the server exactly as an
MCP host would. Async tests run on the anyio pytest plugin, which ships with the
SDK's own dependency set.
"""

import json
import os
import shutil
import tempfile

import pytest

pytest.importorskip("mcp", reason="mcp SDK not installed")

from mcp import Client  # noqa: E402

from evidence_first.evidence.store import EvidenceStore  # noqa: E402
from evidence_first.mcp.server import create_server  # noqa: E402
from evidence_first.receipt import verify_receipt_hash  # noqa: E402

EXPECTED_TOOLS = {
    "work_start",
    "evidence_record",
    "claim_create",
    "verify",
    "approval_request",
    "work_finish",
    "receipt_get",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def call(client, tool, arguments=None):
    """Call a tool and return its structured payload."""
    result = await client.call_tool(tool, arguments or {})
    assert result.is_error is False, f"{tool} failed: {[c.text for c in result.content]}"
    return json.loads(result.content[0].text)


async def call_expecting_error(client, tool, arguments=None):
    result = await client.call_tool(tool, arguments or {})
    if result.is_error:
        return None, " ".join(getattr(c, "text", "") for c in result.content)
    return json.loads(result.content[0].text), ""


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
async def client(store):
    server = create_server(store=store, workdir=os.getcwd())
    async with Client(server) as session:
        yield session


# --- Server startup and discovery -------------------------------------------

@pytest.mark.anyio
async def test_server_initializes_successfully(client):
    tools = await client.list_tools()
    assert tools is not None
    assert client.server_info is not None


@pytest.mark.anyio
async def test_all_expected_tools_are_discoverable(client):
    names = {tool.name for tool in (await client.list_tools()).tools}
    assert names == EXPECTED_TOOLS


@pytest.mark.anyio
async def test_tool_descriptions_mention_evidence_and_approval(client):
    tools = {tool.name: (tool.description or "") for tool in (await client.list_tools()).tools}
    assert "evidence" in tools["evidence_record"].lower()
    assert "approval" in tools["approval_request"].lower()
    assert "receipt" in tools["work_finish"].lower()


# --- Lifecycle --------------------------------------------------------------

@pytest.mark.anyio
async def test_full_work_lifecycle_produces_verified_receipt(client):
    started = await call(client, "work_start", {
        "task": "Fix authentication bug",
        "agent": "simulated coding agent",
        "objective": "Authentication tests pass",
    })
    assert started["ok"] is True
    assert started["status"] == "running"
    run_id = started["run_id"]

    evidence = await call(client, "evidence_record", {
        "run_id": run_id,
        "type": "run_tests",
        "description": "Ran the authentication test suite",
        "source": "pytest",
        "content": "tests/test_auth.py::test_login PASSED\n12 passed in 0.42s",
    })
    assert evidence["ok"] is True
    assert evidence["evidence_id"].startswith("ev_")

    claim = await call(client, "claim_create", {
        "run_id": run_id,
        "statement": "All authentication tests pass.",
        "check": {"type": "keyword_present", "keyword": "12 passed"},
        "evidence_ids": [evidence["evidence_id"]],
    })
    assert claim["ok"] is True
    assert claim["status"] == "NOT_VERIFIED"

    verified = await call(client, "verify", {"claim_id": claim["claim_id"]})
    assert verified["ok"] is True
    assert verified["status"] == "VERIFIED"
    assert verified["reason"]
    assert verified["evidence"] == [evidence["evidence_id"]]

    finished = await call(client, "work_finish", {"run_id": run_id})
    assert finished["ok"] is True
    assert finished["status"] == "completed"
    assert finished["result"] == "VERIFIED"
    assert finished["receipt_hash"].startswith("sha256:")

    receipt = (await call(client, "receipt_get", {"run_id": run_id}))["receipt"]
    assert receipt["run_id"] == run_id
    assert receipt["result"]["status"] == "VERIFIED"
    assert receipt["integrity"]["hash"] == finished["receipt_hash"]
    assert verify_receipt_hash(receipt) is True


@pytest.mark.anyio
async def test_duplicate_evidence_is_recorded_once(client):
    run_id = (await call(client, "work_start", {"task": "Fix bug", "agent": "agent"}))["run_id"]
    first = await call(client, "evidence_record", {
        "run_id": run_id, "type": "run_tests", "description": "Ran tests", "content": "12 passed",
    })
    second = await call(client, "evidence_record", {
        "run_id": run_id, "type": "run_tests", "description": "Ran tests", "content": "12 passed",
    })
    assert second["evidence_id"] == first["evidence_id"]


# --- Verification -----------------------------------------------------------

@pytest.mark.anyio
async def test_claim_without_evidence_is_insufficient(client):
    run_id = (await call(client, "work_start", {"task": "Fix bug", "agent": "agent"}))["run_id"]
    claim = await call(client, "claim_create", {
        "run_id": run_id,
        "statement": "All authentication tests pass.",
        "check": {"type": "keyword_present", "keyword": "passed"},
    })
    outcome = await call(client, "verify", {"claim_id": claim["claim_id"]})
    assert outcome["status"] == "INSUFFICIENT_EVIDENCE"
    assert outcome["evidence_considered"] == 0


@pytest.mark.anyio
async def test_claim_with_no_check_is_not_verified(client):
    run_id = (await call(client, "work_start", {"task": "Fix bug", "agent": "agent"}))["run_id"]
    await call(client, "evidence_record", {
        "run_id": run_id, "type": "run_tests", "description": "Ran tests", "content": "12 passed",
    })
    claim = await call(client, "claim_create", {"run_id": run_id, "statement": "Everything is fine."})
    outcome = await call(client, "verify", {"claim_id": claim["claim_id"]})
    assert outcome["status"] == "NOT_VERIFIED"


@pytest.mark.anyio
async def test_failed_verification_is_reported_truthfully(client):
    run_id = (await call(client, "work_start", {"task": "Fix bug", "agent": "agent"}))["run_id"]
    await call(client, "evidence_record", {
        "run_id": run_id, "type": "run_tests", "description": "Ran tests", "content": "1 failed in 0.2s",
    })
    claim = await call(client, "claim_create", {
        "run_id": run_id,
        "statement": "All authentication tests pass.",
        "check": {"type": "keyword_present", "keyword": "12 passed"},
    })
    outcome = await call(client, "verify", {"claim_id": claim["claim_id"]})
    finished = await call(client, "work_finish", {"run_id": run_id})
    assert outcome["status"] == "FAILED"
    assert finished["result"] == "FAILED"


# --- Approval safety --------------------------------------------------------

@pytest.mark.anyio
async def test_consequential_action_is_blocked_pending_approval(client, store):
    run_id = (await call(client, "work_start", {"task": "Deploy the fix", "agent": "agent"}))["run_id"]
    gate = await call(client, "approval_request", {
        "run_id": run_id,
        "action": "send_email",
        "reason": "Email the release manager",
    })
    assert gate["approval_required"] is True
    assert gate["status"] == "pending"
    assert gate["blocked"] is True
    assert gate["requested_permission"] == "SEND"

    finished = await call(client, "work_finish", {"run_id": run_id})
    assert finished["result"] == "NEEDS_REVIEW"
    assert finished["counts"]["pending_approvals"] == 1

    pending = store.get_pending_approvals(run_id)
    assert len(pending) == 1
    actions = [a for a in store.get_actions(run_id) if a["action_id"] == gate["action_id"]]
    assert actions[0]["status"] == "approval_pending"


@pytest.mark.anyio
async def test_mcp_exposes_no_way_to_approve(client):
    names = {tool.name for tool in (await client.list_tools()).tools}
    assert not any("approve" in name or "decide" in name for name in names)


@pytest.mark.anyio
async def test_unclassified_action_fails_closed(client):
    run_id = (await call(client, "work_start", {"task": "Do something", "agent": "agent"}))["run_id"]
    gate = await call(client, "approval_request", {
        "run_id": run_id, "action": "frobnicate_widgets", "reason": "unknown consequence",
    })
    assert gate["approval_required"] is True
    assert gate["requested_permission"] == "UNKNOWN_PERMISSION"


@pytest.mark.anyio
async def test_mcp_cannot_downgrade_a_consequential_action(client):
    """Declaring a lower permission must not clear the approval gate."""
    run_id = (await call(client, "work_start", {"task": "Notify the team", "agent": "agent"}))["run_id"]
    gate = await call(client, "approval_request", {
        "run_id": run_id, "action": "send_email", "reason": "Email the release manager", "permission": "READ",
    })
    assert gate["approval_required"] is True
    assert gate["requested_permission"] == "SEND"
    assert gate["blocked"] is True


@pytest.mark.anyio
async def test_mcp_rejects_an_unknown_permission_value(client):
    run_id = (await call(client, "work_start", {"task": "Do something", "agent": "agent"}))["run_id"]
    payload, _ = await call_expecting_error(client, "approval_request", {
        "run_id": run_id, "action": "do_thing", "reason": "r", "permission": "SUPER",
    })
    assert payload["ok"] is False
    assert "Unknown permission" in payload["error"]


@pytest.mark.anyio
async def test_pending_approval_prevents_a_verified_result(client):
    """Verified claims must not mask a blocked consequential action."""
    run_id = (await call(client, "work_start", {"task": "Ship it", "agent": "agent"}))["run_id"]
    await call(client, "evidence_record", {
        "run_id": run_id, "type": "run_tests", "description": "Ran tests", "content": "3 passed",
    })
    claim = await call(client, "claim_create", {
        "run_id": run_id,
        "statement": "All authentication tests pass.",
        "check": {"type": "keyword_present", "keyword": "3 passed"},
    })
    assert (await call(client, "verify", {"claim_id": claim["claim_id"]}))["status"] == "VERIFIED"
    await call(client, "approval_request", {
        "run_id": run_id, "action": "send_email", "reason": "Notify the release manager",
    })
    finished = await call(client, "work_finish", {"run_id": run_id})
    receipt = (await call(client, "receipt_get", {"run_id": run_id}))["receipt"]

    assert receipt["claims"][0]["status"] == "VERIFIED"
    assert finished["result"] == "NEEDS_REVIEW"
    assert receipt["result"]["verified"] == 1
    assert receipt["result"]["pending_approvals"] == 1
    assert receipt["approvals"][0]["status"] == "pending"


@pytest.mark.anyio
async def test_pending_approval_survives_receipt_retrieval(client, store):
    run_id = (await call(client, "work_start", {"task": "Deploy", "agent": "agent"}))["run_id"]
    await call(client, "approval_request", {"run_id": run_id, "action": "submit_tender", "reason": "submit"})
    await call(client, "work_finish", {"run_id": run_id})
    receipt = (await call(client, "receipt_get", {"run_id": run_id}))["receipt"]

    assert receipt["approvals"][0]["status"] == "pending"
    assert receipt["result"]["pending_approvals"] == 1
    assert receipt["result"]["status"] == "NEEDS_REVIEW"
    assert verify_receipt_hash(receipt) is True


# --- Receipt integrity ------------------------------------------------------

@pytest.mark.anyio
async def test_receipt_hash_changes_when_content_changes(client):
    first_run = (await call(client, "work_start", {"task": "Fix bug", "agent": "agent"}))["run_id"]
    await call(client, "evidence_record", {
        "run_id": first_run, "type": "run_tests", "description": "Ran tests", "content": "12 passed",
    })
    first = (await call(client, "work_finish", {"run_id": first_run}))["receipt_hash"]

    second_run = (await call(client, "work_start", {"task": "Fix bug", "agent": "agent"}))["run_id"]
    await call(client, "evidence_record", {
        "run_id": second_run, "type": "run_tests", "description": "Ran tests", "content": "1 failed",
    })
    second = (await call(client, "work_finish", {"run_id": second_run}))["receipt_hash"]

    assert first != second


@pytest.mark.anyio
async def test_receipt_hash_is_stable_across_retrieval(client):
    run_id = (await call(client, "work_start", {"task": "Fix bug", "agent": "agent"}))["run_id"]
    await call(client, "evidence_record", {
        "run_id": run_id, "type": "inspect_file", "description": "Read auth.py", "content": "def login(): ...",
    })
    finished = await call(client, "work_finish", {"run_id": run_id})
    again = (await call(client, "receipt_get", {"run_id": run_id}))["receipt"]
    assert again["integrity"]["hash"] == finished["receipt_hash"]
    assert verify_receipt_hash(again) is True


@pytest.mark.anyio
async def test_receipt_never_exposes_secrets(client):
    run_id = (await call(client, "work_start", {"task": "Deploy", "agent": "agent"}))["run_id"]
    await call(client, "evidence_record", {
        "run_id": run_id,
        "type": "http_call",
        "description": "Called the deploy API",
        "content": "HTTP 200 OK\nAuthorization: Bearer sk-abcdefghijklmnopqrstuvwxyz123456",
    })
    await call(client, "work_finish", {"run_id": run_id})
    receipt = (await call(client, "receipt_get", {"run_id": run_id}))["receipt"]
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in json.dumps(receipt)


# --- Resources --------------------------------------------------------------

@pytest.mark.anyio
async def test_receipt_resource_is_readable(client):
    run_id = (await call(client, "work_start", {"task": "Fix bug", "agent": "agent"}))["run_id"]
    await call(client, "evidence_record", {
        "run_id": run_id, "type": "inspect_file", "description": "Read auth.py", "content": "code",
    })
    await call(client, "work_finish", {"run_id": run_id})

    templates = (await client.list_resource_templates()).resource_templates
    uris = {t.uri_template for t in templates}
    assert {"receipt://run/{run_id}", "evidence://run/{run_id}", "audit://run/{run_id}"} <= uris

    contents = (await client.read_resource(f"receipt://run/{run_id}")).contents
    payload = json.loads(contents[0].text)
    assert payload["run_id"] == run_id
    assert payload["integrity"]["hash"].startswith("sha256:")


@pytest.mark.anyio
async def test_evidence_and_audit_resources(client):
    run_id = (await call(client, "work_start", {"task": "Fix bug", "agent": "agent"}))["run_id"]
    await call(client, "evidence_record", {
        "run_id": run_id, "type": "run_tests", "description": "Ran tests", "content": "12 passed",
    })
    await call(client, "work_finish", {"run_id": run_id})

    evidence = json.loads((await client.read_resource(f"evidence://run/{run_id}")).contents[0].text)
    assert len(evidence["evidence"]) >= 1

    audit = json.loads((await client.read_resource(f"audit://run/{run_id}")).contents[0].text)
    kinds = [event["event"] for event in audit["events"]]
    assert kinds[0] == "run_started"
    assert "action" in kinds


@pytest.mark.anyio
async def test_resource_for_unknown_run_errors(client):
    with pytest.raises(Exception):
        await client.read_resource("receipt://run/run_missing")


# --- Prompt -----------------------------------------------------------------

@pytest.mark.anyio
async def test_verify_work_prompt_is_exposed(client):
    prompts = await client.list_prompts()
    assert "verify_work" in [p.name for p in prompts.prompts]
    rendered = await client.get_prompt("verify_work", {})
    text = rendered.messages[0].content.text
    assert "work_start" in text
    assert "claim_create" in text
    assert "work_finish" in text
    assert "receipt_get" in text
    assert "authoritative" in text.lower()


# --- Error handling ---------------------------------------------------------

@pytest.mark.anyio
async def test_invalid_run_id_is_reported(client):
    for tool, args in (
        ("evidence_record", {"run_id": "run_missing", "type": "x", "description": "y"}),
        ("claim_create", {"run_id": "run_missing", "statement": "y"}),
        ("approval_request", {"run_id": "run_missing", "action": "send_email", "reason": "y"}),
        ("work_finish", {"run_id": "run_missing"}),
        ("receipt_get", {"run_id": "run_missing"}),
    ):
        payload, _ = await call_expecting_error(client, tool, args)
        assert payload is not None, f"{tool} should return a structured error"
        assert payload["ok"] is False
        assert "Run not found" in payload["error"]


@pytest.mark.anyio
async def test_invalid_claim_id_is_reported(client):
    payload, _ = await call_expecting_error(client, "verify", {"claim_id": "claim_missing"})
    assert payload["ok"] is False
    assert "Claim not found" in payload["error"]


@pytest.mark.anyio
async def test_finish_nonexistent_run_is_reported(client):
    payload, _ = await call_expecting_error(client, "work_finish", {"run_id": "run_missing"})
    assert payload["ok"] is False


@pytest.mark.anyio
async def test_invalid_state_transition_is_rejected(client):
    run_id = (await call(client, "work_start", {"task": "Fix bug", "agent": "agent"}))["run_id"]
    await call(client, "work_finish", {"run_id": run_id})
    payload, _ = await call_expecting_error(client, "evidence_record", {
        "run_id": run_id, "type": "run_tests", "description": "late evidence", "content": "12 passed",
    })
    assert payload["ok"] is False
    assert "completed" in payload["error"]


@pytest.mark.anyio
async def test_unknown_check_type_is_rejected(client):
    run_id = (await call(client, "work_start", {"task": "Fix bug", "agent": "agent"}))["run_id"]
    payload, _ = await call_expecting_error(client, "claim_create", {
        "run_id": run_id, "statement": "Looks fine", "check": {"type": "vibes"},
    })
    assert payload["ok"] is False
    assert "Unknown check type" in payload["error"]


@pytest.mark.anyio
async def test_malformed_arguments_are_rejected_by_the_sdk(client):
    result = await client.call_tool("work_start", {})
    assert result.is_error is True
    assert any("Field required" in getattr(c, "text", "") for c in result.content)


@pytest.mark.anyio
async def test_missing_required_parameter_names_are_reported(client):
    result = await client.call_tool("evidence_record", {"run_id": "run_x"})
    assert result.is_error is True


@pytest.mark.anyio
async def test_empty_arguments_are_rejected(client):
    run_id = (await call(client, "work_start", {"task": "Fix bug", "agent": "agent"}))["run_id"]
    payload, _ = await call_expecting_error(client, "evidence_record", {
        "run_id": run_id, "type": "run_tests", "description": "   ",
    })
    assert payload["ok"] is False
    assert "description" in payload["error"]


# --- Real stdio transport ---------------------------------------------------

@pytest.mark.anyio
async def test_server_runs_over_stdio_transport(tmp_path):
    """End-to-end check of the primary local integration path.

    Spawns ``python -m evidence_first.mcp.server`` as a subprocess and drives it
    with the SDK's stdio client, exactly as a local MCP host would.
    """
    import sys

    from mcp.client.stdio import StdioServerParameters

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    db_path = str(tmp_path / "stdio.db")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "evidence_first.mcp.server", "--db", db_path],
        env=dict(os.environ, PYTHONPATH=root),
    )

    async with Client(params, read_timeout_seconds=30) as session:
        names = {tool.name for tool in (await session.list_tools()).tools}
        assert names == EXPECTED_TOOLS

        started = await call(session, "work_start", {"task": "Fix auth bug", "agent": "stdio smoke test"})
        run_id = started["run_id"]
        await call(session, "evidence_record", {
            "run_id": run_id, "type": "run_tests", "description": "Ran tests", "content": "3 passed",
        })
        claim = await call(session, "claim_create", {
            "run_id": run_id,
            "statement": "All authentication tests pass.",
            "check": {"type": "keyword_present", "keyword": "3 passed"},
        })
        assert (await call(session, "verify", {"claim_id": claim["claim_id"]}))["status"] == "VERIFIED"
        finished = await call(session, "work_finish", {"run_id": run_id})
        assert finished["result"] == "VERIFIED"

        receipt = json.loads((await session.read_resource(f"receipt://run/{run_id}")).contents[0].text)
        assert receipt["integrity"]["hash"] == finished["receipt_hash"]

    # The subprocess wrote to the database we pointed it at.
    store = EvidenceStore(db_path=db_path)
    try:
        assert store.get_run(run_id) is not None
    finally:
        store.close()
