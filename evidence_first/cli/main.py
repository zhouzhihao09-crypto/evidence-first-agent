from __future__ import annotations

import sys
import os

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from evidence_first.runtime.run import AgentRuntime
from evidence_first.evidence.store import EvidenceStore
from evidence_first.tools.filesystem import FilesystemTool
from evidence_first.tools.search import DocumentSearchTool
from evidence_first.agent.planner import Planner
from evidence_first.llm.mock import MockLLMProvider


console = Console()


def _make_runtime(auto_approve: bool = False, store: EvidenceStore = None) -> AgentRuntime:
    store = store or EvidenceStore()
    tools = [FilesystemTool(), DocumentSearchTool()]
    llm = MockLLMProvider()
    runtime = AgentRuntime(llm=llm, tools=tools, auto_approve=auto_approve, store=store)
    return runtime


def _status_icon(status: str) -> str:
    if status == "completed":
        return "[green]DONE[/green]"
    elif status == "failed":
        return "[red]FAIL[/red]"
    elif status == "approval_pending":
        return "[yellow]APPROVAL[/yellow]"
    elif status == "denied":
        return "[red]DENIED[/red]"
    elif status == "pending":
        return "[dim]PENDING[/dim]"
    return "[dim]PENDING[/dim]"


@click.group()
def main() -> None:
    pass


@main.command()
@click.argument("task_file")
@click.option("--auto-approve", is_flag=True, help="Auto-approve all permission requests")
def run(task_file: str, auto_approve: bool) -> None:
    from evidence_first.models import Task
    import yaml

    if not os.path.isfile(task_file):
        console.print(f"[red]Task file not found: {task_file}[/red]")
        sys.exit(1)

    with open(task_file, "r") as f:
        task_data = yaml.safe_load(f)

    task_description = task_data.get("description", task_data.get("task", ""))
    if not task_description:
        console.print("[red]No task description found in file[/red]")
        sys.exit(1)

    console.print(Panel(f"Starting agent run for: {task_description}", title="Evidence-First Agent"))

    runtime = _make_runtime(auto_approve=auto_approve)
    run = runtime.run_task(task_description)

    console.print(f"\n[bold]Run ID:[/bold] {run.run_id}")
    console.print(f"[bold]Task:[/bold] {run.task_description}")
    console.print(f"[bold]Status:[/bold] {run.status}")

    console.print("\n[bold]Progress:[/bold]")
    for step in run.steps:
        status = step.get("status", "unknown")
        icon = _status_icon(status)
        console.print(f"  {icon} Step {step.get('step')}: {step.get('type')} via {step.get('tool')} — {status}")

    if run.approval_requests:
        console.print("\n[bold]Approval Requests:[/bold]")
        for req in run.approval_requests:
            console.print(f"  [yellow]{req.get('approval_id')}[/yellow]: {req.get('requested_permission')} — {req.get('reason')}")

    console.print(f"\n[bold]Run completed with {len(run.steps)} steps.[/bold]")


@main.command()
@click.argument("run_id")
def inspect(run_id: str) -> None:
    store = EvidenceStore()
    run = store.get_run(run_id)
    if not run:
        console.print(f"[red]Run not found: {run_id}[/red]")
        sys.exit(1)

    console.print(Panel(f"Run: {run_id}", title="Evidence Inspector"))
    console.print(f"[bold]Task:[/bold] {run.get('task_description', 'N/A')}")
    console.print(f"[bold]Status:[/bold] {run.get('status', 'N/A')}")

    actions = store.get_actions(run_id)
    if actions:
        table = Table(title="Actions")
        table.add_column("Action ID", style="cyan", no_wrap=True)
        table.add_column("Type")
        table.add_column("Tool")
        table.add_column("Status")
        table.add_column("Reason", max_width=40)
        for a in actions:
            table.add_row(
                a["action_id"], a.get("type", ""), a.get("tool", ""),
                a.get("status", ""), a.get("reason", "")[:40],
            )
        console.print(table)

    observations = store.get_observations(run_id)
    if observations:
        table = Table(title="Observations")
        table.add_column("ID", style="yellow", no_wrap=True)
        table.add_column("Action ID", style="cyan", no_wrap=True)
        table.add_column("Content", max_width=60)
        for o in observations:
            table.add_row(
                o["observation_id"], o.get("action_id", ""),
                (o.get("content", "")[:60] if o.get("content") else ""),
            )
        console.print(table)

    evidence = store.get_evidence(run_id)
    if evidence:
        table = Table(title="Evidence")
        table.add_column("Evidence ID", style="green", no_wrap=True)
        table.add_column("Source")
        table.add_column("Hash")
        table.add_column("Snippet", max_width=50)
        for e in evidence:
            table.add_row(
                e["evidence_id"], e.get("filename", e.get("source_type", "")),
                e.get("hash", ""), (e.get("snippet", "")[:50] if e.get("snippet") else ""),
            )
        console.print(table)

    verifications = store.get_verifications(run_id)
    if verifications:
        table = Table(title="Verifications")
        table.add_column("ID", style="magenta", no_wrap=True)
        table.add_column("Action ID", style="cyan", no_wrap=True)
        table.add_column("Verified")
        table.add_column("Confidence")
        table.add_column("Reason", max_width=50)
        for v in verifications:
            verified = "Yes" if v.get("verified") else "No"
            table.add_row(
                v["verification_id"], v.get("action_id", ""), verified,
                str(v.get("confidence", "")), (v.get("reason", "")[:50] if v.get("reason") else ""),
            )
        console.print(table)

    edges = store.get_edges()
    if edges:
        table = Table(title="Evidence Graph Edges")
        table.add_column("Source")
        table.add_column("Source Type")
        table.add_column("Target")
        table.add_column("Target Type")
        table.add_column("Relationship")
        for e in edges:
            table.add_row(e.get("source_id", ""), e.get("source_type", ""),
                          e.get("target_id", ""), e.get("target_type", ""),
                          e.get("relationship", ""))
        console.print(table)


if __name__ == "__main__":
    main()
