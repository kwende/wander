# Wander agent instructions

Wander is the authoritative structured ledger for ongoing work. Preserve these
boundaries:

## Fresh-machine bootstrap

When the user gives Codex this repository and asks to set Wander up on another
Windows machine, use this contract instead of reconstructing the installation:

1. Confirm `python` (3.11 or newer), `git`, and `codex` are available on `PATH`.
2. Clone or update the repository at any writable absolute path. Wander scripts
   derive the repository root; do not rewrite them with a machine-specific path.
3. From the repository root, run:

   ```powershell
   & .\scripts\install-codex.ps1
   ```

   This idempotently creates/updates `.venv`, installs the package, copies the
   Wander skill to the user's `.codex\skills\wander`, and registers the global
   `wander` MCP server against the current checkout.
4. Verify `codex mcp get wander` names this checkout's `scripts\start-mcp.ps1`
   and the installed skill contains `SKILL.md`.
5. Tell the user to restart Codex. Registration changes do not become reliable
   in an already-running Codex process.
6. Start the read-only dashboard separately with
   `& .\scripts\start-web.ps1`. Verify `/api/health` reports `mode: read-only`
   and a reachable `share_base_url`. If multi-NIC detection selects the wrong
   adapter, restart with `-ShareBaseUrl 'http://<reachable-ip>:8765'`.
7. After Codex restarts, call Wander `list_journeys` and then create or resume
   the requested journey. Do not claim installation succeeded merely because
   the Python package installed.

Use the global MCP plus user-skill installation model above. Do not also install
the local plugin: exposing both creates duplicate Wander integrations backed by
the same database.

`data\wander.db` is intentionally gitignored. A new checkout starts with a new
ledger. To migrate existing workboards, stop the Wander MCP and web processes,
copy the SQLite database together with any `-wal`/`-shm` companions, then start
Wander and verify the journey list. Never copy a live SQLite database piecemeal.

Run `& .\scripts\install-codex.ps1 -PlanOnly` to inspect the resolved checkout,
Python, MCP entry point, and skill destination without changing the machine.

## Runtime invariants

- SQLite is the source of truth.
- The HTTP dashboard is read-only. Do not add mutation routes.
- State changes use semantic MCP tools rather than generic SQL or CRUD tools.
- Every mutation requires `expected_version`, advances the journey version, and
  appends a provenance event in the same transaction.
- Completion requires concrete evidence.
- A parent work item cannot complete while any child is pending, active, or
  blocked. Active and blocked parents expose their children as the dashboard's
  visible acceptance checklist; completed parents collapse that detail.
- When code implements a completed item, preserve verified GitHub commit links
  as structured work-item provenance; absence of code is not a completion
  failure.
- GitHub issue integration is a one-way projection. SQLite remains authoritative;
  GitHub checkbox state, comment edits, and comment deletion must never mutate
  Wander. The issue title/body and unrelated comments remain human-owned. One
  registered conversation comment is replaced wholesale after meaningful
  Wander batches through the configured GitHub MCP.
- `prepare_github_issue_projection` is read-only. Creating the first projection
  comment is followed by `register_github_projection_comment`, which stores the
  stable `#issuecomment-...` URL as a versioned journey link. Regenerate the
  comment afterward because registration advances the Wander journey version.
- If the registered GitHub comment is missing or an update fails, leave Wander
  unchanged, report the projection as stale, and do not create a duplicate
  comment automatically.
- Before first-comment creation, search comments for the exact stable journey
  marker. Reuse one match to recover an interrupted registration, create only
  when there are zero matches, and stop on multiple matches. Marker discovery
  recovers identity only and never imports GitHub content into Wander.
- Treat `owner_thread_id` as a soft workflow bumper, not authorization. A
  non-owner write succeeds and returns `writer_warning`; surface that warning
  prominently instead of hiding it or describing the write as forbidden.
- Do not infer or scrape Codex thread IDs from internal session storage. Attach
  only supported `codex://threads/<thread-id>` links supplied through a stable
  product surface.
- Prefer `attach_current_thread` when the host exposes `CODEX_THREAD_ID` or
  `CODEX_SESSION_ID`; ask for a copied deep link and use `attach_thread` only
  when host identity is unavailable.
- A globally registered MCP process may not inherit per-thread environment
  values. Bind its session once with `identify_thread`; never switch that
  identity within the same server process.
- Cross-thread delegation exposes the sender as `source_thread_id`; that is not
  the target conversation's identity. Read the target command environment's
  `CODEX_THREAD_ID` or `CODEX_SESSION_ID` instead.

## Active-work detail and resumability

Keep the journey's stable milestone list concise, but optimize the active work
path for interruption recovery rather than brevity. When an active item has two
or more known steps that can be started, completed, verified, blocked,
delegated, or returned to independently, add those steps as child work items.

Build this checklist just in time as the execution path becomes known. Do not
pre-expand speculative future work or record command-by-command mechanics. The
deepest active child should communicate the user's present focus, while its
parents explain why that work matters and its siblings show where to return
next. A checkpoint preserves causal context, decisions, and invariants; it is
not a substitute for the visible checklist of current execution steps.

Completed work and future milestones may remain compact. Add detail when it is
needed to survive distraction, context switching, delegation, or a handoff.

## Information-model feedback

Wander is experimental infrastructure. While using it, notice when missing
structured context causes repeated repository scans, long chat-history
reconstruction, avoidable tool calls, assumptions, or ambiguous handoffs. At a
useful stopping point, briefly suggest additional information that Wander could
store or expose when doing so would materially reduce token cost, latency, or
future investigative work.

Keep this feedback bounded:

- Do not interrupt the current engineering task merely to redesign Wander.
- Do not add database columns, tables, MCP tools, or dashboard behavior without
  explicit authorization. A recommendation is not implementation approval.
- Recommend structured storage only for information that is recurring,
  queryable, and useful across sessions. Keep one-off narrative in checkpoints,
  evidence, notes, or links.
- Prefer references, hashes, result paths, and compact summaries over copying
  large logs or source artifacts into SQLite.
- Distinguish a database-model gap from a skill, workflow, or presentation gap;
  recommend the smallest layer that removes the observed friction.

A useful recommendation states:

1. What the agent had to reconstruct or infer.
2. What evidence showed the cost was recurring or material.
3. The smallest field, relationship, semantic tool, or derived view that would
   remove that cost.
4. How the data would be populated, refreshed, and kept authoritative.
5. Any migration, staleness, privacy, or ledger-bloat risk.

If the existing Wander model was sufficient, say so when the user is evaluating
the tool. Do not invent optimization work merely because this feedback path
exists.

At the beginning of participating work, call `get_resume_context`. After a
meaningful semantic batch, update the relevant item and record a checkpoint
containing the current model, last decision, invariants, open questions, and
best resume point. If the journey has exactly one linked GitHub issue, refresh
its registered Wander projection comment after the batch.

