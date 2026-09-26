# Changelog

All notable changes to this project are documented here.
This project follows a lightweight versioning scheme.

## [0.2.0] — AI Work Receipts + Verification

The evidence runtime is unchanged. This release adds the receipt layer, claim
verification, and MCP distribution on top of it.

### Added

- **AI Work Receipt** (`evidence_first/receipt.py`) — a versioned, portable JSON
  summary of a run: run metadata, actions, evidence references, claims,
  verifications, approvals, result, and an integrity block.
- **Deterministic receipt hashing** — canonical JSON (sorted keys, compact
  separators, UTF-8) hashed with SHA-256 into `integrity.hash`. The hash never
  covers itself; the same logical receipt always produces the same digest.
  `verify_receipt_hash()` re-checks a receipt, and tampering is detected.
- **Claims** — a `Claim` model and `claims` table (the only schema addition,
  created automatically). Claims are statements that can be verified.
- **Deterministic verification checks** (`evidence_first/verification.py`) —
  `keyword_present`, `keyword_absent`, `file_exists`, `sha256_match`,
  `evidence_count`, `verification_status_equals`, plus `register_check()` for
  custom checks. Canonical claim states: `VERIFIED`, `FAILED`,
  `INSUFFICIENT_EVIDENCE`, `NOT_VERIFIED`.
- **Work run lifecycle** (`evidence_first/work.py`) — `WorkLedger` with
  `start_run`, `record_evidence`, `create_claim`, `verify_claim`,
  `request_approval`, `finish_run`, `get_receipt`, and a flattened audit trail.
  Reuses the existing recorders, permission map, and approval manager.
- **Secret redaction** (`evidence_first/redaction.py`) — API keys, bearer
  tokens, JWTs, private key blocks, passwords, and credential-shaped
  environment assignments are redacted on write and again on receipt build.
- **MCP v2 server** (`evidence_first/mcp/`) — built on the official Python SDK
  v2 API (`mcp.server.mcpserver.MCPServer`). Seven tools (`work_start`,
  `evidence_record`, `claim_create`, `verify`, `approval_request`,
  `work_finish`, `receipt_get`), three resources (`receipt://run/{run_id}`,
  `evidence://run/{run_id}`, `audit://run/{run_id}`), and one prompt
  (`verify_work`). stdio and streamable-http transports.
- **CLI** — `receipt` command (`--json`, `--db`) and `mcp` command, added to
  the existing `evidence-agent` group; new `evidence-first` entry point
  exposing `receipt`, `inspect`, `mcp`. Existing `run` and `inspect` commands
  are unchanged.
- **Examples** — `examples/coding_agent/auth_fix.py` (simulated coding agent
  producing a VERIFIED receipt) and `examples/tender/receipt_demo.py` (the
  original tender workflow summarized as a receipt: 11 claims, 10 verified,
  1 insufficient, `NEEDS_REVIEW`).
- **Tests** — 125 new tests (`test_receipt.py`, `test_work.py`, `test_mcp.py`,
  `test_security.py`), including MCP in-memory client tests, a real stdio
  subprocess handshake, and regression tests that `finish_run` never verifies
  claims on the agent's behalf. 237 total, all offline.
- **Docs** — README rewritten around AI Work Receipts, updated architecture
  diagram, new screenshots and a 36-second demo GIF, plus
  `scripts/make_visuals.py` to regenerate the visuals from real output.

### Changed

- `fastapi` minimum raised to `>=0.141.0`: the MCP SDK 2.x requires
  `starlette>=1.7`, which the previous fastapi pin did not allow.
- `Approval` gained a `run_id` field, matching the existing `approvals` table
  (the runtime previously only tracked approvals in the run's JSON blob).
- Agent-reported action names are resolved to a permission by a conservative
  keyword rule (`send_email` → `SEND`), because they are not tool names in the
  existing permission map. Unrecognised names resolve to `UNKNOWN_PERMISSION`
  and are gated.
- Evidence, actions, observations, verifications, conclusions, and claims are
  now redacted on write.
- Project version bumped to `0.2.0`.

### Security notes

- MCP exposes no tool that can approve an action, and unrecognised
  agent-reported actions fail closed behind the approval gate.
- An explicitly declared `permission` can raise the required permission but
  never lower one resolved from the action name, and an unrecognised
  permission value is rejected. A pending approval forces `NEEDS_REVIEW` even
  when every claim verified.
- Claim checks that touch the filesystem are confined to the working
  directory, matching the existing filesystem tool restriction.
- The streamable-http transport has **no authentication**. Bind it to
  `127.0.0.1` and treat it as a single-user local tool.

### Not production ready

This remains a local developer tool: no authentication, no multi-user support,
no encryption at rest, no remote storage.

## [0.1.0] — Evidence-first agent framework

Initial release: task → action → observation → evidence → verification →
conclusion chain, SQLite evidence store, evidence graph, permission model with
human approval gates, tender requirement verification strategies, mock LLM
provider, CLI, and the Evidence Explorer web UI. See `PHASE2_REPORT.md`.
