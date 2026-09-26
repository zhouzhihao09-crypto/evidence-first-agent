"""Example 1 — a coding agent that produces a work receipt.

Scenario: fix an authentication bug.

Everything is simulated and deterministic. No Claude/Codex API, no network, no
credentials. The point is the shape of the work: record what you did, state
what you claim, let the engine decide whether the claim holds, then finish the
run and get a hashable receipt.

    python examples/coding_agent/auth_fix.py
    python examples/coding_agent/auth_fix.py --with-approval   # shows the gate
    python examples/coding_agent/auth_fix.py --db .evidence_first/demo.db
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from evidence_first.evidence.store import EvidenceStore  # noqa: E402
from evidence_first.receipt import render_receipt_text  # noqa: E402
from evidence_first.work import WorkLedger  # noqa: E402

AGENT = "simulated coding agent"
TASK = "Fix the authentication bug in session validation"
OBJECTIVE = "Session tokens are rejected after logout, and the regression test proves it"

# Simulated tool output. A real agent would capture this from its own tools.
PYTEST_OUTPUT = """\
============================= test session starts ==============================
tests/test_auth.py::test_session_rejected_after_logout PASSED   [ 50%]
tests/test_auth.py::test_session_survives_active_use PASSED     [ 75%]
tests/test_auth.py::test_token_signature_enforced PASSED        [100%]
============================== 3 passed in 0.31s ==============================
"""

SECURITY_SCAN_OUTPUT = """\
scanner: bandit-like
finding count: 0
severity high: 0
severity critical: 0
result: no high or critical findings
"""

GIT_DIFF = """\
--- a/app/session.py
+++ b/app/session.py
@@ validate_session @@
-    if token in store:      # token was never removed on logout
+    if token in store and not revoked(tokens, token):
         return Session(user_id)
"""

FILES_INSPECTED = ["app/session.py", "app/auth.py", "tests/test_auth.py"]


def run_example(db_path: str, with_approval: bool = False) -> str:
    tmpdir = None
    if db_path is None:
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "evidence.db")

    store = EvidenceStore(db_path=db_path)
    ledger = WorkLedger(store=store, workdir=os.getcwd())
    try:
        # 1. Start a verifiable work run.
        run = ledger.start_run(task=TASK, agent=AGENT, objective=OBJECTIVE)
        print(f"[1] work run started: {run.run_id}")

        # 2. Record the files that were inspected.
        for path in FILES_INSPECTED:
            ledger.record_evidence(
                run.run_id,
                "inspect_file",
                f"Inspected {path}",
                source=path,
                content=f"traced session validation through {path}",
            )
        print(f"[2] recorded {len(FILES_INSPECTED)} inspected files")

        # 3. Record the code change.
        diff_evidence = ledger.record_evidence(
            run.run_id,
            "edit_file",
            "Patched session validation to honour token revocation",
            source="app/session.py",
            content=GIT_DIFF,
        )
        print("[3] recorded the code change (git diff)")

        # 4. Record test results.
        test_evidence = ledger.record_evidence(
            run.run_id,
            "run_tests",
            "Ran tests/test_auth.py",
            source="pytest",
            content=PYTEST_OUTPUT,
        )
        print("[4] recorded test output")

        # 5. Record the security scan.
        scan_evidence = ledger.record_evidence(
            run.run_id,
            "security_scan",
            "Ran the security scanner over the change",
            source="bandit-like",
            content=SECURITY_SCAN_OUTPUT,
        )
        print("[5] recorded the security scan")

        # 6./7. State claims, then verify them with deterministic checks.
        # Each claim links the evidence it is actually about, so a check never
        # reads unrelated output.
        claims = [
            (
                "The authentication regression tests pass.",
                {"type": "keyword_present", "keyword": "3 passed"},
                [test_evidence["evidence_id"]],
            ),
            (
                "The security scan reports no high or critical findings.",
                # Deterministic: a high finding would print "severity high: 1".
                {"type": "keyword_absent", "keyword": "severity high: 1"},
                [scan_evidence["evidence_id"]],
            ),
            (
                "The session module was actually modified.",
                {"type": "keyword_present", "keyword": "revoked(tokens, token)"},
                [diff_evidence["evidence_id"]],
            ),
        ]
        for statement, check, evidence_ids in claims:
            claim = ledger.create_claim(run.run_id, statement, check=check, evidence_ids=evidence_ids)
            outcome = ledger.verify_claim(claim["claim_id"])
            print(f"[6] {outcome['status']:<18} {statement}")

        if with_approval:
            # 8. Consequential work is gated, not self-approved.
            gate = ledger.request_approval(
                run.run_id,
                "deploy_production",
                "Ship the session fix to production",
            )
            print(f"[7] approval gate: {gate['status']} ({gate['requested_permission']})")

        # 9. Finish the run and print the receipt.
        finished = ledger.finish_run(run.run_id)
        print(f"[8] run finished: result={finished['result']}")
        print()
        text = render_receipt_text(finished["receipt"])
        print(text)
        return text
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", dest="db_path", default=None, help="Evidence database path")
    parser.add_argument(
        "--with-approval",
        action="store_true",
        help="Also request approval for a consequential action (result becomes NEEDS_REVIEW)",
    )
    args = parser.parse_args()
    run_example(args.db_path, with_approval=args.with_approval)


if __name__ == "__main__":
    main()
