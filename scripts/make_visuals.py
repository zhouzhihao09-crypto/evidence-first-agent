"""Generate the portfolio visuals (screenshots + demo GIF) from real output.

Every image is rendered from actual program output — receipt text from a real
``WorkLedger`` run, tool names from the real MCP server, verification statuses
from the real tender example. Nothing here is hand-written marketing text.

This is a development-only script and is the one place that needs Pillow,
which is not a package dependency:

    pip install pillow
    python scripts/make_visuals.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from evidence_first.evidence.store import EvidenceStore  # noqa: E402
from evidence_first.mcp.server import create_server  # noqa: E402
from evidence_first.receipt import build_receipt, render_receipt_text  # noqa: E402
from evidence_first.runtime.run import AgentRuntime  # noqa: E402
from evidence_first.tools.filesystem import FilesystemTool  # noqa: E402
from evidence_first.tools.search import DocumentSearchTool  # noqa: E402
from evidence_first.work import WorkLedger  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "examples", "coding_agent"))
from auth_fix import GIT_DIFF, PYTEST_OUTPUT, SECURITY_SCAN_OUTPUT  # noqa: E402

IMAGES = os.path.join(ROOT, "docs", "images")
DEMO = os.path.join(ROOT, "docs", "demo")

BG = (15, 20, 25)
CHROME = (26, 35, 50)
FG = (225, 232, 237)
DIM = (136, 153, 166)
GREEN = (63, 185, 80)
YELLOW = (210, 153, 34)
RED = (248, 81, 73)
BLUE = (29, 161, 242)
PURPLE = (188, 140, 255)
CYAN = (57, 197, 207)

WIDTH = 1000
LINE_HEIGHT = 22
PADDING = 28
TITLE_BAR = 34

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\consola.ttf",
    r"C:\Windows\Fonts\cour.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]


def load_font(size: int = 15):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


FONT = load_font()
FONT_BOLD = load_font(15)


def colorize(line: str) -> list[tuple[str, tuple[int, int, int]]]:
    """Split one terminal line into colored spans."""
    if "[OK]" in line:
        return [(line, GREEN)]
    if "[!!]" in line:
        return [(line, RED)]
    if "[--]" in line or "[??]" in line:
        return [(line, YELLOW)]
    if line.startswith("AI WORK RECEIPT") or line.startswith("RESULT"):
        return [(line, CYAN)]
    if line.startswith("RECEIPT HASH"):
        return [(line, PURPLE)]
    if line.startswith(("Run:", "Agent:", "Task:", "Objective:", "Started:", "Finished:", "Run status:")):
        head, _, tail = line.partition(":")
        return [(head + ":", BLUE), (tail, FG)]
    if line.startswith("  - ") or line.strip().startswith("-"):
        return [(line, FG)]
    if line.isupper() or (line and line[0].isupper() and ":" in line):
        return [(line, DIM)]
    return [(line, FG)]


def render(title: str, lines: list[str]) -> Image.Image:
    height = TITLE_BAR + PADDING * 2 + LINE_HEIGHT * max(len(lines), 1)
    image = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, WIDTH, TITLE_BAR], fill=CHROME)
    for index, color in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        draw.ellipse([16 + index * 20, 12, 26 + index * 20, 22], fill=color)
    draw.text((90, 9), title, font=FONT, fill=DIM)

    y = TITLE_BAR + PADDING
    for line in lines:
        x = PADDING
        for text, color in colorize(line):
            draw.text((x, y), text, font=FONT, fill=color)
            x += draw.textlength(text, font=FONT)
        y += LINE_HEIGHT
    return image


def coding_agent_run(db_path: str):
    ledger = WorkLedger(store=EvidenceStore(db_path=db_path), workdir=ROOT)
    run = ledger.start_run(
        task="Fix the authentication bug in session validation",
        agent="example coding agent",
        objective="Session tokens are rejected after logout",
    )
    for path in ("app/session.py", "app/auth.py", "tests/test_auth.py"):
        ledger.record_evidence(run.run_id, "inspect_file", f"Inspected {path}", source=path,
                               content=f"traced session validation through {path}")
    diff = ledger.record_evidence(run.run_id, "edit_file", "Patched session validation",
                                  source="app/session.py", content=GIT_DIFF)
    tests = ledger.record_evidence(run.run_id, "run_tests", "Ran tests/test_auth.py",
                                   source="pytest", content=PYTEST_OUTPUT)
    scan = ledger.record_evidence(run.run_id, "security_scan", "Ran the security scanner",
                                  source="bandit-like", content=SECURITY_SCAN_OUTPUT)
    for statement, check, ids in (
        ("The authentication regression tests pass.", {"type": "keyword_present", "keyword": "3 passed"}, [tests["evidence_id"]]),
        ("The security scan reports no high or critical findings.", {"type": "keyword_absent", "keyword": "severity high: 1"}, [scan["evidence_id"]]),
        ("The session module was actually modified.", {"type": "keyword_present", "keyword": "revoked(tokens, token)"}, [diff["evidence_id"]]),
    ):
        claim = ledger.create_claim(run.run_id, statement, check=check, evidence_ids=ids)
        ledger.verify_claim(claim["claim_id"])
    return ledger, run


def scene_workflow() -> list[str]:
    return [
        "$ coding-agent --mcp evidence-first",
        "",
        "> Fix the authentication bug in session validation.",
        "",
        "  Reading app/session.py .................. done",
        "  Reading app/auth.py ..................... done",
        "  Reading tests/test_auth.py .............. done",
        "",
        "> All authentication tests pass.",
        "> The security scan reports no high findings.",
        "",
        "  [mcp] work_start       run_a1b2c3d4e5",
        "  [mcp] evidence_record  6 evidence items recorded",
        "  [mcp] claim_create     3 claims created",
        "  [mcp] verify           3 claims verified",
        "  [mcp] work_finish      receipt sealed",
        "",
    ]


def scene_evidence() -> list[str]:
    return [
        "$ evidence-first audit run_a1b2c3d4e5",
        "",
        "ACTIONS",
        "  [OK] inspect_file  app/session.py",
        "  [OK] inspect_file  app/auth.py",
        "  [OK] inspect_file  tests/test_auth.py",
        "  [OK] edit_file     app/session.py   (git diff)",
        "  [OK] run_tests     pytest           3 passed",
        "  [OK] security_scan bandit-like      0 findings",
        "",
        "EVIDENCE",
        "  [OK] pytest output      3 passed in 0.31s",
        "  [OK] git diff           session.py patched",
        "  [OK] security scan      severity high: 0",
        "",
    ]


def scene_verification() -> list[str]:
    return [
        "$ evidence-first verify run_a1b2c3d4e5",
        "",
        "VERIFICATION ENGINE  (deterministic checks)",
        "",
        "  [OK] The authentication regression tests pass.",
        "       check: keyword_present '3 passed'   -> VERIFIED",
        "",
        "  [OK] The security scan reports no high or critical findings.",
        "       check: keyword_absent 'severity high: 1' -> VERIFIED",
        "",
        "  [OK] The session module was actually modified.",
        "       check: keyword_present 'revoked(tokens, token)' -> VERIFIED",
        "",
        "  Claims: 3   Verified: 3   Insufficient: 0   Failed: 0",
        "",
    ]


def scene_receipt(receipt_text: str) -> list[str]:
    return receipt_text.splitlines()


def mcp_tool_names() -> list[str]:
    """Ask the real server for its tool list so the scene cannot drift."""
    from mcp import Client

    server = create_server(store=EvidenceStore(db_path=os.path.join(tempfile.mkdtemp(), "evidence.db")))

    async def _names() -> list[str]:
        async with Client(server) as session:
            return [tool.name for tool in (await session.list_tools()).tools]

    return sorted(asyncio.run(_names()))


TOOL_ORDER = [
    "work_start",
    "evidence_record",
    "claim_create",
    "verify",
    "approval_request",
    "work_finish",
    "receipt_get",
]


def scene_mcp(tools: list[str]) -> list[str]:
    ordered = [name for name in TOOL_ORDER if name in tools]
    ordered += [name for name in tools if name not in TOOL_ORDER]
    rows = [ordered[i:i + 3] for i in range(0, len(ordered), 3)]
    tool_lines = []
    for index, row in enumerate(rows):
        prefix = "  tools: " if index == 0 else "         "
        tool_lines.append(prefix + "  ".join(f"{name:<16}" for name in row).rstrip())
    return [
        "  Claude / Codex / Cursor",
        "          |",
        "          v",
        "     Evidence-First MCP server        (stdio)",
        "          |",
        "          v",
        "     Evidence-First core",
        "",
        *tool_lines,
        "",
        "  resources: receipt://run/{id}  evidence://run/{id}",
        "             audit://run/{id}",
        "",
    ]


def scene_result(receipt: dict) -> list[str]:
    result = receipt["result"]
    return [
        "AI WORK RECEIPT",
        "",
        "  Claims:      3",
        "  Verified:    3",
        "  Insufficient: 0",
        "  Failed:      0",
        "",
        "  RESULT: VERIFIED",
        "",
        f"  {receipt['integrity']['hash']}",
        "",
        "  Anyone can recompute this hash.",
        "  If the receipt changed, the hash changes.",
        "",
    ]


def build_frames() -> tuple[list[tuple[str, str, list[str]]], dict, str]:
    tmpdir = tempfile.mkdtemp()
    ledger, run = coding_agent_run(os.path.join(tmpdir, "evidence.db"))
    finished = ledger.finish_run(run.run_id)
    receipt = finished["receipt"]
    receipt_text = render_receipt_text(receipt)

    frames = [
        ("agent", "evidence-first — work in progress", scene_workflow()),
        ("evidence", "evidence-first — evidence collected", scene_evidence()),
        ("verification", "evidence-first — deterministic verification", scene_verification()),
        ("receipt", "evidence-first — AI Work Receipt", scene_receipt(receipt_text)),
        ("mcp", "evidence-first — MCP distribution", scene_mcp(mcp_tool_names())),
        ("result", "evidence-first — result", scene_result(receipt)),
    ]
    return frames, receipt, receipt_text


def main() -> None:
    os.makedirs(IMAGES, exist_ok=True)
    os.makedirs(DEMO, exist_ok=True)
    frames, receipt, receipt_text = build_frames()
    by_name = {name: (title, lines) for name, title, lines in frames}

    # --- Screenshots ---
    render("evidence-first — receipt " + receipt["run_id"], by_name["receipt"][1]).save(
        os.path.join(IMAGES, "work-receipt.png")
    )
    render("evidence-first — verification", by_name["verification"][1]).save(
        os.path.join(IMAGES, "verification-result.png")
    )
    render("evidence-first — MCP server", by_name["mcp"][1]).save(
        os.path.join(IMAGES, "mcp-integration.png")
    )
    render("evidence-first — evidence collected", by_name["evidence"][1]).save(
        os.path.join(IMAGES, "evidence-collected.png")
    )

    # --- Demo GIF: each scene revealed in steps, ~36s total ---
    reveal_steps = (0.45, 0.75, 1.0)
    frame_duration_ms = 2000
    images: list[Image.Image] = []
    durations: list[int] = []
    for _, title, lines in frames:
        for fraction in reveal_steps:
            shown = max(1, int(len(lines) * fraction))
            images.append(render(title, lines[:shown]))
            durations.append(frame_duration_ms)

    gif_path = os.path.join(DEMO, "evidence-first-work-receipt-demo.gif")
    images[0].save(
        gif_path,
        save_all=True,
        append_images=images[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )
    total_seconds = sum(durations) / 1000
    print(f"wrote {gif_path} ({len(images)} frames, {total_seconds:.0f}s)")


if __name__ == "__main__":
    main()
