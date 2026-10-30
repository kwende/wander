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
7. After Codex restarts, call Wander `list_projects` and then create or resume
   the requested project. Do not claim installation succeeded merely because
   the Python package installed.

Use the global MCP plus user-skill installation model above. Do not also install
the local plugin: exposing both creates duplicate Wander integrations backed by
the same database.

`data\wander.db` is intentionally gitignored. A new checkout starts with a new
ledger. To migrate existing workboards, stop the Wander MCP and web processes,
copy the SQLite database together with any `-wal`/`-shm` companions, then start
Wander and verify the project list. Never copy a live SQLite database piecemeal.

Run `& .\scripts\install-codex.ps1 -PlanOnly` to inspect the resolved checkout,
Python, MCP entry point, and skill destination without changing the machine.

## Runtime invariants

- SQLite is the source of truth.
- The HTTP dashboard is read-only. Do not add mutation routes.
- State changes use semantic MCP tools rather than generic SQL or CRUD tools.
- Every mutation requires `expected_version`, advances the project version, and
  appends a provenance event in the same transaction.
- Completion requires concrete evidence.
- A parent work item cannot complete while any child is pending, active, or
  blocked. Active and blocked parents expose their children as the dashboard's
  visible acceptance checklist; completed parents collapse that detail.
- When code implements a completed item, preserve verified GitHub commit links
  as structured work-item provenance; absence of code is not a completion
  failure.
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

At the beginning of participating work, call `get_resume_context`. After a
meaningful semantic batch, update the relevant item and record a checkpoint
containing the current model, last decision, invariants, open questions, and
best resume point.

