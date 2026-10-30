---
name: wander
description: Create or maintain a durable Wander workboard when the user says "make a Wander," asks to track a task in Wander, or wants a high-level visual checklist that survives across turns. Do not activate for ordinary one-off tasks unless the user explicitly asks for Wander.
---

# Wander

Use the `wander` MCP server as the authoritative state for a high-level project
checklist. Keep implementation detail in checkpoints and evidence; keep work
item titles instantly understandable in the dashboard.

## Start or connect a project

1. Call `list_projects` before creating anything. Reuse a clearly matching
   active project; do not create duplicates from similar conversation wording.
2. Before the first mutation, read `CODEX_THREAD_ID` (or `CODEX_SESSION_ID`) from
   the host environment and call `identify_thread` with
   `codex://threads/<thread-id>`. This environment value is a supported host
   surface; do not scrape Codex session files. If neither value is available,
   ask the user for the copied deep link.
3. When creating a project, derive a short title, plain-language objective, and
   slug from the task. Ask only when a materially different interpretation is
   plausible.
4. Call `attach_current_thread` with a meaningful conversation title and role.
   If host thread identity is unavailable, ask the user for the copied
   `codex://threads/<thread-id>` link and call `attach_thread`.
5. Add a small ordered set of semantic work items that communicates what
   happened, where work is now, and what remains. Prefer roughly 3-7 items; use
   more only when the user needs the additional milestones.
6. Start the item that represents the actual current activity. Do not mark an
   item complete without concrete evidence.

After setup, give the user these local dashboard forms when the web server is
available:

```text
Dashboard: http://localhost:8765/
Shared project: http://localhost:8765/?project=<slug>&shared=1
```

## Continue tracked work

- Begin by calling `get_resume_context`; do not reconstruct state from chat
  history when Wander already records it.
- Pass the last-read project `version` to every mutation. On a stale-version
  conflict, reload, understand the intervening change, and retry only if the
  intended update still makes sense.
- Surface a non-empty `writer_warning`, but continue when the requested write
  is still appropriate. It is a workflow bumper, not an authorization failure.
- Update work-item state after meaningful progress. Record a checkpoint at a
  semantic stopping point, context switch, important decision, blocker, or
  handoff—not after every minor edit.
- If the plan changes, add or abandon items while preserving their history.
- Keep the dashboard concise. Put causal detail, invariants, open questions,
  and the best resume point in the checkpoint rather than work-item titles.

Wander supplements the repository and chat; it does not authorize unrelated
source changes, deployment, publication, or other external side effects.

