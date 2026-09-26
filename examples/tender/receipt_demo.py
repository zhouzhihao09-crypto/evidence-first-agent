"""Example 2 — the original tender workflow, now with a work receipt.

The tender evaluation is unchanged: the same documents, the same deterministic
strategies, the same result.

    10 VERIFIED
     1 EVIDENCE_FOUND_BUT_INSUFFICIENT   (insurance expired before the deadline)

What is new is only the summary layer. The run is projected into an AI Work
Receipt, which reports 11 claims, 10 verified, 1 insufficient, and an overall
result of NEEDS_REVIEW. The evidence status stays truthful: a receipt never
rounds "insufficient" up to "verified".

    python examples/tender/receipt_demo.py
    python examples/tender/receipt_demo.py --db .evidence_first/tender.db
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from evidence_first.evidence.store import EvidenceStore  # noqa: E402
from evidence_first.receipt import build_receipt, render_receipt_text  # noqa: E402
from evidence_first.runtime.run import AgentRuntime  # noqa: E402
from evidence_first.tools.filesystem import FilesystemTool  # noqa: E402
from evidence_first.tools.search import DocumentSearchTool  # noqa: E402

TASK = "Determine whether a company has the documents required for a tender"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", dest="db_path", default=None, help="Evidence database path")
    args = parser.parse_args()

    db_path = args.db_path
    tmpdir = None
    if db_path is None:
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "evidence.db")

    store = EvidenceStore(db_path=db_path)
    try:
        runtime = AgentRuntime(
            tools=[FilesystemTool(), DocumentSearchTool()],
            auto_approve=True,
            store=store,
        )
        run = runtime.run_task(TASK)
        print(f"Run ID: {run.run_id}")
        print(f"Task:   {run.task_description}")
        print(f"Status: {run.status}")
        print()

        receipt = build_receipt(store, run.run_id)
        result = receipt["result"]
        print("CLAIMS")
        for claim in receipt["claims"]:
            print(f"  {claim['status']:<24} {claim['statement']}  ({claim['engine_status']})")
        print()
        print(f"Claims:     {result['claims_total']}")
        print(f"Verified:   {result['verified']}")
        print(f"Insufficient: {result['insufficient_evidence']}")
        print(f"Overall:    {result['status']}")
        print()
        print(render_receipt_text(receipt))
    finally:
        store.close()


if __name__ == "__main__":
    main()
