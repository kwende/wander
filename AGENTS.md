# Wander agent instructions

Wander is the authoritative structured ledger for ongoing work. Preserve these
boundaries:

- SQLite is the source of truth.
- The HTTP dashboard is read-only. Do not add mutation routes.
- State changes use semantic MCP tools rather than generic SQL or CRUD tools.
- Every mutation requires `expected_version`, advances the project version, and
  appends a provenance event in the same transaction.
- Completion requires concrete evidence.
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

At the beginning of participating work, call `get_resume_context`. After a
meaningful semantic batch, update the relevant item and record a checkpoint
containing the current model, last decision, invariants, open questions, and
best resume point.

