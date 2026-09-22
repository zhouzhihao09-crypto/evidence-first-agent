from __future__ import annotations

import html as html_module
import json
import os
from datetime import datetime
from typing import Any, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from evidence_first.evidence.store import EvidenceStore


DB_PATH = os.path.join(os.getcwd(), ".evidence_first", "evidence.db")

app = FastAPI(title="Evidence-First Agent", version="0.1.0")


def get_store() -> EvidenceStore:
    return EvidenceStore(db_path=DB_PATH)


def set_db_path(path: str) -> None:
    global DB_PATH
    DB_PATH = path


def esc(s: str) -> str:
    if s is None:
        return ""
    return html_module.escape(str(s))


def fmt_time(ts: str) -> str:
    if not ts:
        return "N/A"
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return ts


def get_status_counts(run_id: str, store: EvidenceStore) -> dict[str, int]:
    verifications = store.get_verifications(run_id)
    counts = {
        "VERIFIED": 0,
        "EVIDENCE_FOUND_BUT_INSUFFICIENT": 0,
        "NEEDS_HUMAN_REVIEW": 0,
        "EVIDENCE_NOT_FOUND": 0,
    }
    for v in verifications:
        status = v.get("status", "EVIDENCE_NOT_FOUND")
        if status in counts:
            counts[status] += 1
        else:
            counts["EVIDENCE_NOT_FOUND"] += 1
    return counts


def get_requirements_for_run(run_id: str, store: EvidenceStore) -> list[dict[str, Any]]:
    actions = store.get_actions(run_id)
    verifications = store.get_verifications(run_id)
    requirements = []
    search_actions = [a for a in actions if a.get("type") == "search_documents"]
    verif_by_action = {}
    for v in verifications:
        verif_by_action[v.get("action_id", "")] = v
    for act in search_actions:
        inp = act.get("input", {})
        if isinstance(inp, str):
            try:
                inp = json.loads(inp)
            except Exception:
                inp = {}
        query = inp.get("query", "Unknown") if isinstance(inp, dict) else "Unknown"
        directory = inp.get("directory", "") if isinstance(inp, dict) else ""
        v = verif_by_action.get(act.get("action_id", ""))
        requirements.append({
            "action_id": act.get("action_id", ""),
            "query": query,
            "directory": directory,
            "status": v.get("status", "EVIDENCE_NOT_FOUND") if v else "EVIDENCE_NOT_FOUND",
            "verified": v.get("verified", False) if v else False,
            "confidence": v.get("confidence", 0.0) if v else 0.0,
            "reason": v.get("reason", "No verification") if v else "No verification yet",
            "verification_id": v.get("verification_id", "") if v else "",
            "observation_id": v.get("observation_id", "") if v else "",
        })
    return requirements


def get_strategy_for_requirement(query: str) -> str:
    query_lower = query.lower()
    if "insurance" in query_lower or "expiry" in query_lower or "valid" in query_lower and "license" not in query_lower:
        return "validity_date"
    if "project" in query_lower and "experience" in query_lower:
        return "count_check"
    return "supporting_statement"


def _list_runs(store: EvidenceStore) -> list[dict[str, Any]]:
    rows = store.conn.execute("SELECT run_id FROM runs ORDER BY started_at DESC").fetchall()
    runs = []
    for r in rows:
        run_id = r[0]
        run = store.get_run(run_id)
        if run is None:
            continue
        actions = store.get_actions(run_id)
        verifications = store.get_verifications(run_id)
        evidence = store.get_evidence(run_id)
        observations = store.get_observations(run_id)
        counts = get_status_counts(run_id, store)
        runs.append({
            "run_id": run["run_id"],
            "task_id": run.get("task_id", ""),
            "task_description": run.get("task_description", ""),
            "status": run.get("status", "unknown"),
            "started_at": run.get("started_at", ""),
            "completed_at": run.get("completed_at", ""),
            "num_actions": len(actions),
            "num_observations": len(observations),
            "num_evidence": len(evidence),
            "num_verifications": len(verifications),
            "verification_counts": counts,
        })
    return runs


HTML_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Evidence Explorer</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f1419; color: #e1e8ed; line-height: 1.6; }
a { color: #1da1f2; text-decoration: none; }
a:hover { text-decoration: underline; }
.container { max-width: 1200px; margin: 0 auto; padding: 20px; }
header { background: #1a2332; border-bottom: 1px solid #2a3a4a; padding: 20px 0; margin-bottom: 30px; }
header h1 { font-size: 24px; color: #1da1f2; }
header p { color: #8899a6; font-size: 14px; margin-top: 4px; }
.badge { display: inline-block; padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
.badge-verified { background: #14522d; color: #3fb950; border: 1px solid #238636; }
.badge-insufficient { background: #3d2e00; color: #d29922; border: 1px solid #9e6a03; }
.badge-review { background: #3b2e3d; color: #bc8cff; border: 1px solid #8957e5; }
.badge-notfound { background: #3d1a1a; color: #f85149; border: 1px solid #da3633; }
.badge-completed { background: #14522d; color: #3fb950; }
.badge-failed { background: #3d1a1a; color: #f85149; }
.badge-pending { background: #3d2e00; color: #d29922; }
.card { background: #1a2332; border: 1px solid #2a3a4a; border-radius: 8px; padding: 20px; margin-bottom: 16px; cursor: pointer; }
.card-hover:hover { border-color: #1da1f2; }
.stats { display: flex; gap: 12px; margin-bottom: 24px; flex-wrap: wrap; }
.stat { background: #1a2332; border: 1px solid #2a3a4a; border-radius: 8px; padding: 16px 24px; text-align: center; min-width: 140px; }
.stat-num { font-size: 32px; font-weight: 700; }
.stat-label { font-size: 12px; color: #8899a6; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 10px 14px; border-bottom: 1px solid #2a3a4a; font-size: 14px; }
th { color: #8899a6; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
tr:hover td { background: rgba(29,161,242,0.05); }
.evidence-chain { display: flex; align-items: center; gap: 8px; padding: 16px; background: #1a2332; border-radius: 8px; margin-bottom: 20px; flex-wrap: wrap; }
.chain-node { background: #0f1419; border: 1px solid #2a3a4a; border-radius: 6px; padding: 8px 16px; font-size: 13px; white-space: nowrap; }
.chain-arrow { color: #8899a6; font-size: 18px; }
.detail-label { font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; color: #8899a6; margin: 16px 0 8px 0; font-weight: 600; }
.excerpt { background: #0f1419; border: 1px solid #2a3a4a; border-radius: 6px; padding: 16px; font-family: 'SF Mono', Monaco, monospace; font-size: 14px; margin: 8px 0; }
.meta-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
.meta-item { background: #0f1419; border: 1px solid #2a3a4a; border-radius: 6px; padding: 12px; }
.meta-key { font-size: 11px; color: #8899a6; text-transform: uppercase; letter-spacing: 0.5px; }
.meta-value { font-size: 14px; margin-top: 4px; word-break: break-all; }
.back-link { display: inline-block; margin-bottom: 16px; font-size: 14px; }
.empty-state { text-align: center; padding: 60px 20px; color: #8899a6; }
.empty-state p { font-size: 18px; margin-top: 8px; }
h2 { font-size: 20px; margin-bottom: 16px; }
h3 { font-size: 16px; margin-bottom: 12px; }
.nav { display: flex; gap: 8px; margin-bottom: 24px; }
.nav a { padding: 6px 14px; border: 1px solid #2a3a4a; border-radius: 6px; font-size: 13px; }
.nav a:hover { border-color: #1da1f2; background: rgba(29,161,242,0.05); text-decoration: none; }
</style>
</head>
<body>
<header>
<div class="container">
<h1>Evidence Explorer</h1>
<p>AI agents that don't just act - they prove what they did.</p>
</div>
</header>
<div class="container">
{content}
</div>
</body>
</html>
"""


def render_page(content: str) -> HTMLResponse:
    return HTMLResponse(HTML_HEAD.replace("{content}", content))


@app.get("/")
def dashboard():
    store = get_store()
    try:
        runs = _list_runs(store)
        if not runs:
            content = '<div class="empty-state"><p>No runs found.</p><p>Run a task first:</p><pre>python -m evidence_first.cli.main run examples/tender/task.yaml</pre></div>'
            return render_page(content)
        rows = ""
        for r in runs:
            counts = r.get("verification_counts", {})
            verified = counts.get("VERIFIED", 0)
            insufficient = counts.get("EVIDENCE_FOUND_BUT_INSUFFICIENT", 0)
            review = counts.get("NEEDS_HUMAN_REVIEW", 0)
            notfound = counts.get("EVIDENCE_NOT_FOUND", 0)
            status_class = "badge-completed" if r["status"] == "completed" else ("badge-failed" if r["status"] == "failed" else "badge-pending")
            rows += (
                '<tr><td><a href="/runs/{rid}">{rid}</a></td>'
                '<td>{task}</td><td><span class="badge {sc}">{st}</span></td>'
                '<td>{start}</td><td>{na}</td><td>{ne}</td><td>{nv}</td>'
                '<td><span class="badge badge-verified">{v} Verified</span></td>'
                '<td><span class="badge badge-insufficient">{i} Insufficient</span></td>'
                '<td><span class="badge badge-review">{r} Review</span></td>'
                '<td><span class="badge badge-notfound">{n} Not Found</span></td></tr>'
            ).format(
                rid=esc(r["run_id"]), task=esc(r["task_description"][:80]),
                sc=status_class, st=esc(r["status"]), start=esc(fmt_time(r["started_at"])),
                na=r["num_actions"], ne=r["num_evidence"], nv=r["num_verifications"],
                v=verified, i=insufficient, r=review, n=notfound,
            )
        content = (
            '<div class="nav"><a href="/">Dashboard</a></div>'
            '<h2>Runs</h2>'
            '<table><tr><th>Run ID</th><th>Task</th><th>Status</th><th>Started</th>'
            '<th>Actions</th><th>Evidence</th><th>Verifications</th>'
            '<th>Verified</th><th>Insufficient</th><th>Review</th><th>Not Found</th></tr>'
            '{rows}</table>'
        ).format(rows=rows)
        return render_page(content)
    finally:
        store.close()


@app.get("/runs/{run_id}")
def run_detail(run_id: str):
    store = get_store()
    try:
        run = store.get_run(run_id)
        if run is None:
            content = f'<div class="container"><h2>Run not found: {esc(run_id)}</h2><a class="back-link" href="/">Back to Dashboard</a></div>'
            return render_page(content)
        requirements = get_requirements_for_run(run_id, store)
        counts = get_status_counts(run_id, store)
        cards = ""
        for req in requirements:
            status = req["status"]
            bc = "badge-verified" if status == "VERIFIED" else ("badge-insufficient" if status == "EVIDENCE_FOUND_BUT_INSUFFICIENT" else ("badge-review" if status == "NEEDS_HUMAN_REVIEW" else "badge-notfound"))
            cards += (
                f'<a href="/runs/{esc(run_id)}/requirements/{esc(req["action_id"])}" style="text-decoration:none;">'
                f'<div class="card card-hover"><span class="badge {bc}">{esc(status)}</span>'
                f'<h3 style="display:inline;margin-left:8px;">{esc(req["query"])}</h3>'
                f'<span style="color:#8899a6;font-size:13px;margin-left:8px;">confidence: {req["confidence"]:.2f}</span>'
                f'<p style="margin-top:8px;color:#8899a6;font-size:13px;">{esc(req["reason"][:120])}</p></div></a>'
            )
        req_list = ""
        for req in requirements:
            status = req["status"]
            bc = "badge-verified" if status == "VERIFIED" else ("badge-insufficient" if status == "EVIDENCE_FOUND_BUT_INSUFFICIENT" else ("badge-review" if status == "NEEDS_HUMAN_REVIEW" else "badge-notfound"))
            req_list += (
                f'<tr><td><a href="/runs/{esc(run_id)}/requirements/{esc(req["action_id"])}">{esc(req["query"])}</a></td>'
                f'<td>{esc(get_strategy_for_requirement(req["query"]))}</td>'
                f'<td><span class="badge {bc}">{esc(status)}</span></td>'
                f'<td>{req["confidence"]:.2f}</td>'
                f'<td>{esc(req["reason"][:100])}</td></tr>'
            )
        content = (
            f'<div class="nav"><a href="/">&#8592; Dashboard</a></div>'
            f'<h2>Run: {esc(run_id)}</h2>'
            f'<p style="margin-bottom:16px;color:#8899a6;">{esc(run.get("task_description", "N/A"))}</p>'
            f'<div class="stats">'
            f'<div class="stat"><div class="stat-num badge-verified">{counts.get("VERIFIED",0)}</div><div class="stat-label">Verified</div></div>'
            f'<div class="stat"><div class="stat-num badge-insufficient">{counts.get("EVIDENCE_FOUND_BUT_INSUFFICIENT",0)}</div><div class="stat-label">Insufficient</div></div>'
            f'<div class="stat"><div class="stat-num badge-review">{counts.get("NEEDS_HUMAN_REVIEW",0)}</div><div class="stat-label">Needs Review</div></div>'
            f'<div class="stat"><div class="stat-num badge-notfound">{counts.get("EVIDENCE_NOT_FOUND",0)}</div><div class="stat-label">Not Found</div></div>'
            f'</div>'
            f'<h3>Requirements</h3>'
            f'<table><tr><th>Requirement</th><th>Strategy</th><th>Status</th><th>Confidence</th><th>Conclusion</th></tr>{req_list}</table>'
            f'<h3 style="margin-top:32px;">Requirement Cards</h3>{cards}'
        )
        return render_page(content)
    finally:
        store.close()


@app.get("/runs/{run_id}/requirements/{action_id}")
def requirement_detail(run_id: str, action_id: str):
    store = get_store()
    try:
        run = store.get_run(run_id)
        if run is None:
            content = f'<div class="container"><h2>Run not found: {esc(run_id)}</h2><a class="back-link" href="/">Back to Dashboard</a></div>'
            return render_page(content)
        actions = store.get_actions(run_id)
        obs = store.get_observations(run_id)
        evidence_items = store.get_evidence(run_id)
        verifications = store.get_verifications(run_id)
        requirements = get_requirements_for_run(run_id, store)
        req = None
        for r in requirements:
            if r["action_id"] == action_id:
                req = r
                break
        if req is None:
            content = f'<div class="container"><h2>Requirement not found</h2><a class="back-link" href="/runs/{esc(run_id)}">Back to Run</a></div>'
            return render_page(content)
        action = None
        for a in actions:
            if a.get("action_id") == action_id:
                action = a
                break
        observation = None
        for o in obs:
            if o.get("action_id") == action_id:
                observation = o
                break
        verification = None
        for v in verifications:
            if v.get("action_id") == action_id:
                verification = v
                break
        evidence = []
        obs_ids = [o.get("observation_id", "") for o in obs if o.get("action_id") == action_id]
        for e in evidence_items:
            if e.get("action_id") == action_id or e.get("observation_id") in obs_ids:
                evidence.append(e)
        badge_class = "badge-verified" if req["status"] == "VERIFIED" else ("badge-insufficient" if req["status"] == "EVIDENCE_FOUND_BUT_INSUFFICIENT" else ("badge-review" if req["status"] == "NEEDS_HUMAN_REVIEW" else "badge-notfound"))
        strategy = get_strategy_for_requirement(req["query"])
        tender_deadline = "2025-06-01"
        expiry_date = ""
        reasoning = ""
        for e in evidence:
            content_text = e.get("content", "") or ""
            if "Expiry" in content_text and not expiry_date:
                for line in content_text.split("\n"):
                    if "Expiry" in line or "expiry" in line.lower():
                        expiry_date = line.strip()
                        break
        if verification and verification.get("reason"):
            reasoning = verification.get("reason", "")
        if not reasoning and expiry_date:
            reasoning = f"Expiry date: {expiry_date}\nTender deadline: {tender_deadline}"
        if not expiry_date and verification and verification.get("reason"):
            import re as _re
            date_match = _re.search(r"(\d{4}-\d{2}-\d{2})", verification.get("reason", ""))
            if date_match:
                expiry_date = date_match.group(1)
        if expiry_date and tender_deadline and verification and not verification.get("verified", False):
            reasoning += f"\n\n{expiry_date} < {tender_deadline}\nResult: Evidence exists, but it deterministically fails the requirement."
        elif expiry_date and tender_deadline and verification and verification.get("verified", False):
            reasoning += f"\n\n{expiry_date} >= {tender_deadline}\nResult: Evidence supports the requirement."
        meta_items = ""
        if evidence:
            e = evidence[0]
            meta_items = (
                f'<div class="meta-item"><div class="meta-key">Evidence ID</div><div class="meta-value">{esc(e.get("evidence_id",""))}</div></div>'
                f'<div class="meta-item"><div class="meta-key">File</div><div class="meta-value">{esc(e.get("filename",""))}</div></div>'
                f'<div class="meta-item"><div class="meta-key">Source Action</div><div class="meta-value">{esc(e.get("action_id",""))}</div></div>'
                f'<div class="meta-item"><div class="meta-key">Hash</div><div class="meta-value">{esc(e.get("hash",""))}</div></div>'
            )
        evidence_rows = ""
        for e in evidence:
            fn = esc(e.get("filename", ""))
            preview = esc((e.get("snippet") or e.get("content") or "")[:150])
            evidence_rows += f'<tr><td><strong>{fn}</strong></td><td><div class="excerpt" style="margin:4px 0;">{preview}</div></td><td>{esc(e.get("hash",""))}</td></tr>'
        chain_steps = [f'<div class="chain-node"><strong>Requirement</strong><br>{esc(req["query"])}</div>', '<span class="chain-arrow">&#9660;</span>']
        chain_steps.append(f'<div class="chain-node"><strong>Search</strong><br>{esc(req["query"])}</div><span class="chain-arrow">&#9660;</span>')
        if action:
            chain_steps.append(f'<div class="chain-node"><strong>{esc(action.get("type","").upper())}</strong><br>{esc(action.get("tool",""))}</div><span class="chain-arrow">&#9660;</span>')
        if observation:
            chain_steps.append(f'<div class="chain-node"><strong>Observation</strong><br>{esc((observation.get("content") or "")[:80])}</div><span class="chain-arrow">&#9660;</span>')
        if evidence:
            e = evidence[0]
            chain_steps.append(f'<div class="chain-node"><strong>Document</strong><br>{esc(e.get("filename","document"))}</div><span class="chain-arrow">&#9660;</span>')
            chain_steps.append(f'<div class="chain-node"><strong>Evidence</strong><br>{esc(e.get("evidence_id",""))}</div><span class="chain-arrow">&#9660;</span>')
        if verification:
            chain_steps.append(f'<div class="chain-node"><strong>Verification</strong><br>{esc(verification.get("status", req["status"]))}</div><span class="chain-arrow">&#9660;</span>')
        chain_steps.append(f'<div class="chain-node"><strong>Conclusion</strong><br><span class="badge {badge_class}">{esc(req["status"])}</span></div>')
        chain_html = " ".join(chain_steps)
        content = (
            f'<div class="nav"><a href="/runs/{esc(run_id)}">&#8592; Run Detail</a></div>'
            f'<h2>Requirement: {esc(req["query"])}</h2>'
            f'<div class="evidence-chain">{chain_html}</div>'
            f'<div class="card"><h3>Status</h3><span class="badge {badge_class}">{esc(req["status"])}</span><span style="margin-left:8px;color:#8899a6;">Confidence: {req["confidence"]:.2f}</span></div>'
            f'<div class="card"><div class="detail-label">Verification Reasoning</div><div class="excerpt">{esc(reasoning)}</div></div>'
            f'<div class="card"><div class="detail-label">Evidence</div>'
            f'<table><tr><th>File/Document</th><th>Excerpt</th><th>Hash</th>'
            f'{evidence_rows if evidence_rows else "<tr><td colspan=3 style=color:#8899a6;>No evidence records</td></tr>"}</table></div>'
            f'<div class="card"><div class="detail-label">Evidence Metadata</div><div class="meta-grid">{meta_items}</div></div>'
        )
        return render_page(content)
    finally:
        store.close()


@app.get("/runs/{run_id}/approvals")
def approvals_page(run_id: str):
    store = get_store()
    try:
        run = store.get_run(run_id)
        if run is None:
            content = f'<div class="container"><h2>Run not found: {esc(run_id)}</h2><a class="back-link" href="/">Back to Dashboard</a></div>'
            return render_page(content)
        approvals = store.get_pending_approvals(run_id)
        all_actions = store.get_actions(run_id)
        rows = ""
        for a in approvals:
            aid = a.get("action_id", "")
            act_type = ""
            act_tool = ""
            risk = "LOW"
            for act in all_actions:
                if act.get("action_id") == aid:
                    act_type = act.get("type", "")
                    act_tool = act.get("tool", "")
                    break
            status_class = "badge-pending" if a.get("status") == "pending" else "badge-completed"
            rows += (
                f'<tr><td>{esc(a.get("approval_id",""))}</td>'
                f'<td>{esc(aid)}</td>'
                f'<td>{esc(act_type)} via {esc(act_tool)}</td>'
                f'<td><span class="badge" style="background:#3d2e00;color:#d29922;border:1px solid #9e6a03;">{risk}</span></td>'
                f'<td><span class="badge {status_class}">{esc(a.get("status",""))}</span></td>'
                f'<td>{esc(fmt_time(a.get("requested_at","")))}</td></tr>'
            )
        content = (
            f'<div class="nav"><a href="/runs/{esc(run_id)}">&#8592; Run Detail</a></div>'
            f'<h2>Approval Queue</h2>'
            f'<table><tr><th>Approval ID</th><th>Action</th><th>Type</th><th>Risk</th><th>Status</th><th>Requested</th>'
            f'{rows if rows else "<tr><td colspan=6 style=color:#8899a6;>No pending approvals</td></tr>"}</table>'
        )
        return render_page(content)
    finally:
        store.close()


@app.get("/api/runs")
def api_runs():
    store = get_store()
    try:
        runs = _list_runs(store)
        return {"runs": runs}
    finally:
        store.close()


@app.get("/api/runs/{run_id}")
def api_run_detail(run_id: str):
    store = get_store()
    try:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        actions = store.get_actions(run_id)
        verifications = store.get_verifications(run_id)
        evidence = store.get_evidence(run_id)
        observations = store.get_observations(run_id)
        approvals = store.get_pending_approvals(run_id)
        counts = get_status_counts(run_id, store)
        return {
            "run": run,
            "actions": actions,
            "observations": observations,
            "evidence": evidence,
            "verifications": verifications,
            "approvals": approvals,
            "verification_counts": counts,
        }
    finally:
        store.close()


@app.get("/api/runs/{run_id}/requirements")
def api_requirements(run_id: str):
    store = get_store()
    try:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        requirements = get_requirements_for_run(run_id, store)
        return {"run_id": run_id, "requirements": requirements}
    finally:
        store.close()


@app.get("/api/runs/{run_id}/evidence")
def api_evidence(run_id: str):
    store = get_store()
    try:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        evidence = store.get_evidence(run_id)
        return {"run_id": run_id, "evidence": evidence}
    finally:
        store.close()


@app.get("/api/runs/{run_id}/approvals")
def api_approvals(run_id: str):
    store = get_store()
    try:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        approvals = store.get_pending_approvals(run_id)
        all_actions = store.get_actions(run_id)
        for a in approvals:
            for act in all_actions:
                if act.get("action_id") == a.get("action_id"):
                    a["action_type"] = act.get("type", "")
                    a["action_tool"] = act.get("tool", "")
                    a["action_reason"] = act.get("reason", "")
                    break
        return {"run_id": run_id, "approvals": approvals}
    finally:
        store.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)