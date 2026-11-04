---
name: wander
description: Create or maintain a durable Wander workboard when the user says "make a Wander," asks to track a task in Wander, wants a high-level visual checklist that survives across turns, supplies a Wander Codex link containing journey and item query parameters, or explicitly asks to add or manage a Jaunt. Do not activate for ordinary one-off tasks unless the user explicitly asks for Wander or a Jaunt.
---

# Wander

Use the `wander` MCP server as the authoritative state for a journey checklist.
Keep stable milestones concise, but give active work enough child detail to
survive distraction and context switching. Keep work-item titles instantly
understandable in the dashboard.

## Start or connect a journey

1. Call `list_journeys` before creating anything. Reuse a clearly matching
   active journey; do not create duplicates from similar conversation wording.
2. When creating a journey, derive a short title, plain-language objective, and
   slug from the task. Ask only when a materially different interpretation is
   plausible.
3. Attach a useful `codex://threads/<thread-id>` deep link with `attach_thread`
   when one is available through a supported host surface or supplied by the
   user. Treat it as ordinary navigation data; it does not identify the MCP
   caller or affect authorization, provenance, concurrency, or mutations.
4. Add a small ordered set of semantic work items that communicates what
   happened, where work is now, and what remains. Prefer roughly 3-7 top-level
   milestones; use more only when the user needs them.
5. Start the item that represents the actual current activity. If it has two or
   more known steps that can be executed or verified independently, add them as
   children so the active path is resumable. Do not mark an item complete
   without concrete evidence.

After setup, give the user these local dashboard forms when the web server is
available:

```text
Dashboard: http://localhost:8765/
Shared journey: http://localhost:8765/?journey=<slug>&shared=1
```

## Continue tracked work

When the user supplies a local Wander Codex link of the form
`http://localhost:8765/?journey=<slug>&item=<work-item-id>` (the host or port
may differ), treat it as an identity pointer rather than sufficient context:

1. Parse the `journey` and `item` query parameters.
2. Call `get_journey` for that journey through Wander MCP.
3. Locate the exact work item by ID. Report clearly if it no longer exists.
4. Reconstruct the useful context from authoritative state: the item, its
   parent, children, completion evidence, attached commit links, current journey
   phase, and latest checkpoint. Do not rely on task wording embedded in chat.
5. Answer or continue work from that reconstructed context.

- Begin by calling `get_resume_context`; do not reconstruct state from chat
  history when Wander already records it.
- Pass the last-read journey `version` to every mutation. On a stale-version
  conflict, reload, understand the intervening change, and retry only if the
  intended update still makes sense.
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

## Manage Jaunts

A Jaunt is an independent, lightweight task with a title, one free-form notes
field, retained history, and an open/completed lifecycle. It is not part of a
Journey and does not use Journey work items, checkpoints, ownership, or GitHub
projection.

- Create a Jaunt only when the user explicitly asks to add or create one. Do
  not turn ordinary side work into a Jaunt merely because it is small.
- Use `list_jaunts` to discover Jaunts and `get_jaunt` to read the selected
  Jaunt and its current version before mutation.
- Use `create_jaunt`, `update_jaunt`, `complete_jaunt`, and `reopen_jaunt` for
  their named lifecycle operations. Pass the last-read or last-returned Jaunt
  version to every mutation after creation.
- Preserve the user's loose notes rather than restructuring them into Journey
  fields. Completing a Jaunt retains its notes and history; reopening makes it
  editable again.
- Never create a Journey labeled as a Jaunt. Jaunt-to-Journey promotion is a
  distinct operation and must not be simulated when no promotion tool exists.
- The human Jaunts modal is available only from localhost. This HTTP boundary
  does not prohibit agents from using the semantic MCP tools.

## Journey progress into GitHub issues

Wander is always the source of truth. GitHub issue checkboxes are a one-way
display projection and must never be read, interpreted, or imported as Wander
state. A human checking, unchecking, renaming, or deleting projected Markdown
does not authorize any Wander mutation.

After a meaningful semantic batch, synchronize a journey only when it contains
exactly one `https://github.com/<owner>/<repo>/issues/<number>` journey link:

1. Call `prepare_github_issue_projection`. It returns the target issue, stable
   marker, exact Markdown comment body, journey version, and either `create` or
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

## Request-only Mathoms

A Mathom is Wander's name for a nugget of knowledge stored in Hippo with
`sourceSystem` fixed to `wander`. Hippo remains the authoritative store; do not
copy Mathoms into Wander's SQLite journey ledger.

- Call `save_mathom` only after the user explicitly asks to preserve the
  specific knowledge in the current conversation.
- Never treat usefulness, novelty, a completed Journey or Jaunt, or a previous
  request to save different knowledge as standing permission.
- Ask for clarification before saving when the claim, confidence, provenance,
  or intended scope is ambiguous.
- Pass the current `codex://threads/<thread-id>` link explicitly as
  `source_thread_link` when saving. This is Mathom provenance supplied as data,
  not an MCP caller identity retained by Wander.
- `search_mathoms` is read-only and does not authorize a later save.
- Do not narrate an unrequested save because unrequested saves are prohibited.

