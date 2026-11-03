---
name: wander
description: Create or maintain a durable Wander workboard when the user says "make a Wander," asks to track a task in Wander, wants a high-level visual checklist that survives across turns, or supplies a Wander Codex link containing project and item query parameters. Do not activate for ordinary one-off tasks unless the user explicitly asks for Wander.
---

# Wander

Use the `wander` MCP server as the authoritative state for a project checklist.
Keep stable milestones concise, but give active work enough child detail to
survive distraction and context switching. Keep work-item titles instantly
understandable in the dashboard.

## Start or connect a project

1. Call `list_projects` before creating anything. Reuse a clearly matching
   active project; do not create duplicates from similar conversation wording.
2. Before the first mutation, execute a read-only host command to read
   `CODEX_THREAD_ID` (or `CODEX_SESSION_ID`) from the command environment, then
   call `identify_thread` with `codex://threads/<thread-id>`. Do not substitute
   a delegation's `source_thread_id`: that identifies the sending conversation,
   not the thread performing this work. The environment value is a supported
   host surface; do not scrape Codex session files. If neither value is
   available, ask the user for the copied deep link.
3. When creating a project, derive a short title, plain-language objective, and
   slug from the task. Ask only when a materially different interpretation is
   plausible.
4. Call `attach_current_thread` with a meaningful conversation title and role.
   If host thread identity is unavailable, ask the user for the copied
   `codex://threads/<thread-id>` link and call `attach_thread`.
5. Add a small ordered set of semantic work items that communicates what
   happened, where work is now, and what remains. Prefer roughly 3-7 top-level
   milestones; use more only when the user needs them.
6. Start the item that represents the actual current activity. If it has two or
   more known steps that can be executed or verified independently, add them as
   children so the active path is resumable. Do not mark an item complete
   without concrete evidence.

After setup, give the user these local dashboard forms when the web server is
available:

```text
Dashboard: http://localhost:8765/
Shared project: http://localhost:8765/?project=<slug>&shared=1
```

## Continue tracked work

When the user supplies a local Wander Codex link of the form
`http://localhost:8765/?project=<slug>&item=<work-item-id>` (the host or port
may differ), treat it as an identity pointer rather than sufficient context:

1. Parse the `project` and `item` query parameters.
2. Call `get_project` for that project through Wander MCP.
3. Locate the exact work item by ID. Report clearly if it no longer exists.
4. Reconstruct the useful context from authoritative state: the item, its
   parent, children, completion evidence, attached commit links, current project
   phase, and latest checkpoint. Do not rely on task wording embedded in chat.
5. Answer or continue work from that reconstructed context.

- Begin by calling `get_resume_context`; do not reconstruct state from chat
  history when Wander already records it.
- Pass the last-read project `version` to every mutation. On a stale-version
  conflict, reload, understand the intervening change, and retry only if the
  intended update still makes sense.
- Surface a non-empty `writer_warning`, but continue when the requested write
  is still appropriate. It is a workflow bumper, not an authorization failure.
- Favor resumability over brevity on the active path. Add child work items just
  in time when steps become independently startable, completable, verifiable,
  blockable, delegable, or returnable. The deepest active child should describe
  the user's current focus; its parents provide purpose and its siblings show
  what comes next.
- Do not pre-expand speculative future work or command-by-command mechanics.
  Checkpoints preserve causal context and invariants, but do not replace the
  visible checklist of current execution steps.
- Update work-item state after meaningful progress. Record a checkpoint at a
  semantic stopping point, context switch, important decision, blocker, or
  handoff—not after every minor edit.
- Before completing a work item, determine whether its implementation produced
  relevant GitHub commits. Pass verified commit URLs through `commit_urls` so
  completion and code provenance are recorded atomically. Do not invent links
  or require them for research, planning, operational, or other non-code work.
  If a commit is discovered later, attach it with `add_work_item_link`.
- If the plan changes, add or abandon items while preserving their history.
- Keep completed work and future milestones concise. Put causal detail,
  invariants, open questions, and the best resume point in the checkpoint
  rather than work-item titles.

## Project progress into GitHub issues

Wander is always the source of truth. GitHub issue checkboxes are a one-way
display projection and must never be read, interpreted, or imported as Wander
state. A human checking, unchecking, renaming, or deleting projected Markdown
does not authorize any Wander mutation.

After a meaningful semantic batch, synchronize a project only when it contains
exactly one `https://github.com/<owner>/<repo>/issues/<number>` project link:

1. Call `prepare_github_issue_projection`. It returns the target issue, stable
   marker, exact Markdown comment body, project version, and either `create` or
   `update`.
2. Use the configured GitHub MCP for the target repository. Do not silently
   substitute `gh`; use a fallback only when the user authorizes it.
3. For `create`, first read all issue conversation comments and match only the
   exact stable marker returned by the plan. One matching comment is an
   interrupted first synchronization: register and reuse its URL. Zero matches
   means call GitHub `add_issue_comment`. More than one match is a conflict;
   stop rather than guessing. Capture the resulting `#issuecomment-<id>` URL,
   then call `register_github_projection_comment` with the version from the
   projection plan. Registration advances the Wander version, so call
   `prepare_github_issue_projection` again and update the registered comment
   with the regenerated body.
4. For `update`, update only the registered conversation comment identified by
   the projection plan. Replace its entire body; do not merge GitHub edits.
5. Read the comment back and verify its stable marker and final Wander version.

Never edit the issue title or body. Never touch unrelated comments. Never
create a second projection comment when a registered comment cannot be found;
report the stale or missing projection and require an explicit recovery
decision. A GitHub failure does not roll back valid Wander state. Report that
the projection is stale and retry after the next meaningful Wander batch.
Reading a marker or registered comment ID is identity recovery only; GitHub
comment content, checkbox state, and wording remain prohibited inputs to
Wander.

Wander supplements the repository and chat; it does not authorize unrelated
source changes, deployment, publication, or other external side effects.

