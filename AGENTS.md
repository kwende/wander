# Wander agent instructions

Wander is the authoritative structured ledger for ongoing work. Preserve these
boundaries:

## Fresh-machine bootstrap

When the user gives Codex this repository and asks to set Wander up on another
Windows machine, use this contract instead of reconstructing the installation:

1. Confirm `python` (3.11 or newer), `git`, and `codex` are available on `PATH`.
2. Clone or update the repository at any writable absolute path. Wander scripts
   derive the repository root; do not rewrite them with a machine-specific path.
3. Build the clean desktop bundle:

   ```powershell
   & .\scripts\build-desktop.ps1
   ```

4. Install the reported bundle. For a new empty ledger, pass `-AllowNewLedger`:

   ```powershell
   & .\scripts\install-desktop.ps1 `
       -BundlePath .\artifacts\bundles\<version>\Wander `
       -AllowNewLedger
   ```

   To migrate an existing ledger, stop its owning Wander process, invoke the
   staged `Wander.exe --import-legacy-database` command documented in `README.md`,
   then publish with `install-desktop.ps1` without `-AllowNewLedger`.
5. Run `& .\scripts\install-codex.ps1`. This copies the Wander skill and
   idempotently registers `http://127.0.0.1:8766/mcp`; it never registers a
   process that can open SQLite independently.
6. Open Wander from the Desktop or Start Menu. Verify
   `/api/health` reports `mode: read-only`, `jaunts_mode: local-authoring` from
   localhost, and a reachable `share_base_url`. Journey HTTP state remains
   read-only; the local Jaunts modal is the one human-authoring exception. If
   multi-NIC detection selects the wrong adapter, launch the executable with an
   explicit `--share-base-url http://<reachable-ip>:8765` argument.
7. Restart Codex, verify `codex mcp get wander` reports the localhost HTTP URL,
   call Wander `list_journeys`, and then create or resume
   the requested journey. Do not claim installation succeeded merely because
   the bundle was built.

Use the global MCP plus user-skill installation model above. Do not also install
the local plugin: exposing both creates duplicate Wander integrations backed by
the same database.

`data\wander.db` is intentionally gitignored. Repository development may use it
only when passed explicitly to the desktop application. Production migration is
owned by packaged `Wander.exe`; never copy a live SQLite database or open it from
PowerShell, Codex, an MCP shim, or an installer.

Run `install-desktop.ps1 -PlanOnly` and `install-codex.ps1 -PlanOnly` to inspect
the resolved application, data, MCP URL, and skill paths without changing the
machine.

## Runtime invariants

- SQLite is the source of truth.
- Journey HTTP state is read-only. The localhost-only Jaunts modal may use its
  narrow semantic Jaunt routes; do not add generic HTTP mutation routes or make
  Jaunts readable from a non-loopback peer.
- Agent state changes use semantic MCP tools rather than generic SQL or CRUD
  tools. The local Jaunts modal is a human-controlled authoring surface.
- A Jaunt is a standalone lightweight task with free-form notes and its own
  version/history. It has no Journey relationship unless a future explicit
  promotion operation records one. Never emulate a Jaunt with a labeled
  Journey or emulate promotion by creating a Journey independently.
- Create a Jaunt through MCP only when the user explicitly asks. Read the Jaunt
  before editing, completing, or reopening it, and pass its last-read version.
- Mathoms are request-only knowledge retained in Hippo with `source_system` set
  to `wander`. Never save a Mathom without an explicit user request concerning
  the specific knowledge. Do not infer permission from novelty, usefulness,
  completed work, or a prior request to save something else.
- Hippo is authoritative for Mathom storage. Do not add a parallel Mathom table
  to Wander's SQLite ledger or expose an HTTP mutation route for Mathoms.
- Every Journey mutation after creation requires `expected_version`, advances
  the Journey version, and appends a provenance event in the same transaction.
  Every Jaunt mutation after creation uses the independent Jaunt version and
  appends a Jaunt event atomically.
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
- Do not infer or scrape Codex thread IDs from internal session storage. Attach
  only supported `codex://threads/<thread-id>` links supplied through a stable
  product surface. A thread link is ordinary navigation data attached through
  `attach_thread`; it does not identify an MCP caller or affect authorization,
  provenance, concurrency, or mutation behavior.

## Active-work detail and resumability

A Journey is a stable objective with a rolling planning horizon, not a promise
that its complete route is known. Treat work items as the current shared map:

- **Now** is the active frontier, including blocked work that still defines the
  current edge of understanding.
- **Next** contains only evidence-supported steps that are understood well
  enough to start. These items are provisional and may be expanded, abandoned,
  or displaced as the Journey reveals new paths.
- **Trail** is completed or deliberately abandoned work. It explains how the
  current frontier was reached without pretending the original route survived
  unchanged.

When the route is uncertain, begin with one active scouting item instead of
inventing an end-to-end roadmap. Scouting reconstructs the relevant terrain,
records important unknowns, and exposes the next useful frontier. It may produce
one next step, several branches, or evidence that the Journey should stop. A
Journey may return to scouting whenever a later step reveals substantial new
terrain.

Optimize the active path for interruption recovery rather than brevity. When an
active item has two or more known steps that can be started, completed,
verified, blocked, delegated, or returned to independently, add those steps as
child work items.

Build this checklist just in time as the execution path becomes known. Do not
pre-expand speculative future work or record command-by-command mechanics. The
deepest active child should communicate the user's present focus, while its
parents explain why that work matters and its siblings show where to return
next. A checkpoint preserves causal context, decisions, and invariants; it is
not a substitute for the visible checklist of current execution steps.

Completed work and known next steps may remain compact. Never add speculative
milestones merely to make a Journey look complete. Add detail when it is needed
to survive distraction, context switching, delegation, or a handoff.

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

