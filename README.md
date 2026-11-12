# Wander

Wander is a durable, multi-thread workboard for rolling-horizon Codex journeys.
A Journey keeps its objective stable while its active frontier and known next
steps evolve through scouting and execution. SQLite is the authoritative state.
Codex mutates that state through semantic MCP tools; the network Journey
dashboard is deliberately read-only. The same application has a localhost-only
modal for human-controlled Jaunts: independent lightweight tasks with free-form
notes and retained history.

Wander also names user-requested nuggets of retained knowledge **Mathoms**.
Mathoms are stored only in Hippo, attributed to the `wander` source system, and
accessed through Wander's MCP tools. They are not part of the SQLite journey
ledger and the dashboard has no Mathom write interface.

## Run locally

```powershell
Set-Location D:\repos\wander
& .\scripts\setup.ps1
& .\scripts\start-web.ps1
```

The desktop entry point starts the HTTP dashboard and a stateless Streamable
HTTP MCP endpoint in-process, verifies both protocols, and then opens a native
WebView2 window. Closing the window shuts down both listeners cleanly. During
development, `scripts\start-web.ps1` remains as a compatibility launcher for
the same desktop application and passes the repository ledger explicitly. The
desktop entry point's production default is
`%LOCALAPPDATA%\Wander\data\wander.db`; `WANDER_DB` is deliberately ignored so
an ambient process environment cannot redirect the authoritative ledger. The
real repository ledger is not migrated to that location until the installation
cutover is run.

The dashboard listens on all network interfaces at port `8765`. Open
`http://localhost:8765` on this machine or `http://<host-ip>:8765` from another
machine on the same reachable network. Windows Firewall may require an inbound
rule. Anyone who can reach the port can read the Journey notes. Jaunt reads and
writes require a loopback TCP peer; LAN clients receive `403` even if they know
the Jaunt API path.

The MCP endpoint listens only on `http://127.0.0.1:8766/mcp`; it is never
exposed on the LAN listener. Use `--mcp-port <port>` only for development or
diagnostic isolation.

The server advertises its preferred LAN IPv4 address to the Share action, so a
link copied from `localhost` remains reachable from another machine. On a VPN or
multi-NIC host, override auto-detection explicitly:

```powershell
& .\scripts\start-web.ps1 -ShareBaseUrl 'http://172.17.5.65:8765'
```

`WANDER_SHARE_BASE_URL` provides the same override for unattended startup. This
is a LAN-reachable address, not discovery of an Internet/NAT public address.

## Build and stage the desktop bundle

The Windows desktop artifact is a PyInstaller one-folder bundle. The build is
identified by the application version plus the full Git commit recorded in its
manifest; dirty builds are marked explicitly and are refused by the installer
unless its development override is supplied.

```powershell
& .\scripts\build-desktop.ps1
```

The build script installs the pinned packaging extra, builds under
`artifacts\bundles`, hashes every bundled file into `wander-build.json`, and
runs the packaged executable's temporary-data lifecycle test. It does not
install the artifact or touch the real ledger.

The first production cutover imports a stopped legacy ledger through the
packaged executable. PowerShell orchestrates the paths but never opens SQLite:

```powershell
& .\artifacts\bundles\<version>\Wander\Wander.exe `
    --import-legacy-database .\data\wander.db `
    --database "$env:LOCALAPPDATA\Wander\data\wander.db" `
    --backups-directory "$env:LOCALAPPDATA\Wander\backups" `
    --diagnostic-output .\artifacts\desktop-import.json
```

The source application must be stopped. The packaged application acquires the
source and destination leases, verifies and snapshots the source, migrates a
staged copy, compares semantic inventories, and atomically publishes the new
ledger. The imported database receives a stable ledger ID and the application
writes `wander.identity.json` beside it. It refuses to overwrite an existing
destination or identity.

An immutable version can be staged without publishing any launch link:

```powershell
& .\scripts\install-desktop.ps1 `
    -BundlePath .\artifacts\bundles\<version>\Wander `
    -StageOnly
```

A full install is data-gated. It requires the per-user ledger to exist first,
unless `-AllowNewLedger` writes a one-use authorization for a new empty ledger.
The application consumes that authorization only after creating the database
and its identity receipt. It will not treat the flag as permission to replace a
missing registered ledger. The installer verifies WebView2 and every manifest
hash, copies into a versioned directory, then transactionally replaces the
Desktop and Start Menu launch links. Pending pointer transactions are restored
on the next installer or rollback run.

```powershell
& .\scripts\install-desktop.ps1 `
    -BundlePath .\artifacts\bundles\<version>\Wander
```

Binary rollback selects the previous verified version and asks that version's
Wander executable to check the current database schema before changing launch
links:

```powershell
& .\scripts\rollback-desktop.ps1
```

Rollback never restores a database backup. Data restore is a separate emergency
operation because it discards writes made after the selected snapshot. Until
the ledger cutover and Codex registration batch is complete, use `-StageOnly`
or temporary install/data roots rather than installing to the default paths.

At startup Wander verifies that the database's ledger ID matches the adjacent
identity receipt. Schema 5 also records `ever_nonempty` inside SQLite; Journey
and Jaunt creation set it through same-transaction triggers. Startup refuses a
missing or replacement database, and refuses a database that was previously
non-empty but now contains neither Journeys nor Jaunts. A pre-schema-5 imported
ledger can establish its first identity only when its recorded import inventory
still proves the current database descends from that import.

The desktop application writes verified snapshots beneath
`%LOCALAPPDATA%\Wander\backups\snapshots`. It snapshots before opening its
listeners, hourly while running, and after stopping its writers during a clean
shutdown. An unchanged semantic inventory is not copied again. Each snapshot is
created with SQLite's backup API, reopened for integrity and foreign-key checks,
hashed, and paired with a manifest containing its ledger identity, schema, and
semantic inventory. The newest 168 changed generations are retained by default;
`--snapshot-interval-seconds` and `--snapshot-retention` provide explicit
runtime overrides.

Restore is an explicit stopped-application operation. It accepts either a
manifested snapshot or the verified first-import backup, takes a pre-restore
safety backup of the current ledger, stages and migrates the selected source,
and publishes it only after identity, schema, integrity, and semantic inventory
checks succeed:

```powershell
& "$env:LOCALAPPDATA\Programs\Wander\versions\<version>\Wander.exe" `
    --restore-ledger "$env:LOCALAPPDATA\Wander\backups\snapshots\<snapshot>.db" `
    --database "$env:LOCALAPPDATA\Wander\data\wander.db" `
    --backups-directory "$env:LOCALAPPDATA\Wander\backups" `
    --diagnostic-output .\artifacts\desktop-restore.json
```

The restore command holds the same exclusive ledger lease as the desktop app.
It leaves `backups\restores\restore-pending.json` only when publication reached
the point where operator recovery is required; rerunning the same restore can
finish that interrupted operation.

Wander writes structured JSON Lines diagnostics to
`%LOCALAPPDATA%\Wander\logs\wander.jsonl`. The file rotates at 5 MB and retains
ten older generations. Every record carries a timestamp, severity, application
version, process/thread identity, and per-run correlation ID. Logged events
cover process and listener lifecycle, ledger validation, HTTP request metadata,
MCP tool names and outcomes, snapshots, imports, restores, and unhandled
exceptions. Tool arguments, HTTP query strings and bodies, Journey/Jaunt text,
Mathom content, credentials, and authorization headers are intentionally not
recorded. An explicit development database keeps logs beside it; use
`--logs-directory` to override that location.

Each journey can expose ordered HTTP links beneath its description. The
dashboard presents top-level work as a rolling horizon: **Now** for active or
blocked work, **Next** for provisional known steps, and **Trail** for completed
or deliberately abandoned work. It does not calculate a completion percentage
over a route that may still change. Active or blocked parent work items expand
into a read-only child checklist so the current acceptance criteria are visible;
completed parents collapse that detail. A completed item can also retain
verified GitHub commit links as code provenance. The shared-journey URL
(`?journey=<slug>&shared=1`) hides journey selection and the Share control while
preserving the journey links and work state.

When a journey contains exactly one GitHub issue link, Codex can project its
current phase and work-item checklist into one dedicated issue comment. The
issue body and unrelated discussion remain untouched. The comment is updated
in place through the configured GitHub MCP after meaningful Wander batches.
This is intentionally one-way: SQLite is always authoritative, and GitHub
checkbox changes are display-only and are never imported into Wander.

The first synchronization creates the comment and records its stable
`#issuecomment-...` URL as a journey link. Later synchronizations replace that
same comment. If the registered comment disappears or GitHub is unavailable,
Wander remains current and the projection is reported stale; Wander does not
create duplicate comments automatically.

On a localhost, non-shared dashboard, each visible work item also exposes a
`Codex link`. It copies a stable journey-and-item identity URL that another
Codex chat resolves through Wander MCP. The control is hidden from shared and
LAN viewers; this is an authoring convenience, not an authentication boundary.

Work explicitly removed from the plan uses the existing `abandoned` state. The
dashboard renders it with a yellow × to distinguish “decided not to do” from a
red blocked or failed-looking condition.

Jaunts open in a separate modal on the local dashboard. A user can create,
edit, complete, reopen, and inspect their history without changing the selected
Journey. Agents have equivalent semantic MCP tools. Jaunts have independent
versions and no Journey relationship; promotion is a separate capability and
must never be simulated by creating a labeled Journey.

A standalone marker line records an external dependency without turning it into
another lifecycle state:

```text
[waiting: Noah to contact Cozad]
```

The local `Waiting` view combines markers from open Jaunts with blocked work
items in active or paused Journeys. For Journey work, include the marker in the
blocking reason. `list_waiting` exposes the same derived, read-only index to
agents. Removing a Jaunt marker or restarting a blocked work item clears it from
the current Waiting view; the original notes and events remain historical.

The repository's `.mcp.json` points to the desktop application's localhost MCP
endpoint. It never starts a second Wander process or opens SQLite. Open Wander
before using repository-scoped MCP tools.

## Codex integration

After installing the desktop application, use the idempotent Codex installer:

```powershell
& .\scripts\install-codex.ps1
```

It copies the user skill, registers the global MCP server at
`http://127.0.0.1:8766/mcp`, and verifies the resulting registration. It also
migrates the recognized legacy checkout registration. Use `-PlanOnly` to
inspect its resolved paths without changing the machine and `-Force` to refresh
an existing matching registration.

Open Wander from the Desktop or Start Menu and restart Codex afterward. The MCP
endpoint exists only while the application is open. A conversation can then say
`Make a Wander out of our task`; the skill discovers or creates the journey,
optionally attaches a supported chat deep link for navigation, and builds only
the planning horizon supported by current evidence. An uncertain Journey may
begin with one scouting item and no invented downstream roadmap. The link is
ordinary Journey data, not MCP caller identity.

To enable Mathom tools, configure the Hippo MCP endpoint and the dedicated
Wander credential before starting Codex:

```powershell
[Environment]::SetEnvironmentVariable(
    "WANDER_HIPPO_URL",
    "http://localhost:8787/mcp",
    "User")
[Environment]::SetEnvironmentVariable(
    "WANDER_HIPPO_TOKEN",
    "<the Hippo Wander-client token>",
    "User")
```

`save_mathom` may be called only after an explicit user request to preserve the
specific knowledge. `search_mathoms` searches or lists only Hippo records whose
source system is `wander`. Restart Codex after changing either variable.

The repository is also a local Agent Plugin package. Its manifest is
`plugin.json`, its MCP declaration is `mcp.json`, and its packaged skill is in
`skills/wander/`. `.agents/plugins/marketplace.json` makes that package
discoverable as `wander-workboard@wander-local` after registering the local
marketplace:

```powershell
codex plugin marketplace add D:\repos\wander
codex plugin list
```

Choose one installation model. Installing the plugin is unnecessary when the
same MCP server and skill are already registered globally; doing both would
expose duplicate integrations backed by the same Wander database.

## State model

```text
Codex chats --semantic MCP tools--> SQLite <--GET Journeys-- network dashboard
                                      ^
                                      +-- localhost Jaunt modal
```

Every Journey mutation after creation:

1. Requires the journey version last read by the caller.
2. Runs in a SQLite `BEGIN IMMEDIATE` transaction.
3. Advances the journey version.
4. Adds an append-only provenance event.

Mutation tools return a compact delta rather than the full journey read model:

```json
{
  "journey": "camera-reboot",
  "version": 3,
  "operation": "work_item.active",
  "changed": {"work_item_ids": ["..."]}
}
```

Use the returned `version` to chain the next mutation. Call `get_journey` only
when the full ledger is needed; use `get_resume_context` for normal resumption.

Jaunts use the same optimistic-concurrency principle but maintain their own
version and event stream. Use `list_jaunts`/`get_jaunt` for reads and
`create_jaunt`, `update_jaunt`, `complete_jaunt`, or `reopen_jaunt` for explicit
operations. Creating or changing a Jaunt never advances a Journey version.

A stale chat receives a conflict instead of overwriting newer work. Work-item
and journey completion require evidence. A parent work item cannot complete
until each child is complete or abandoned.

GitHub projection follows a separate eventual-consistency boundary:

```text
SQLite --prepare_github_issue_projection--> Markdown comment --GitHub MCP--> issue
GitHub --------------------------------------------------------------X--> SQLite
```

`prepare_github_issue_projection` is read-only. Registering the initially
created comment is a normal versioned Wander mutation, after which the comment
must be regenerated at the new journey version.

Codex chat links are explicit Journey references used only for navigation. They
do not identify the MCP caller and do not participate in authorization,
provenance, concurrency, or version checks.

## Project-to-journey compatibility

Wander 0.7 renames its formal work aggregate from project to journey. On first
startup, an existing SQLite ledger is migrated transactionally without rewriting
historical event descriptions. Existing dashboard links using `?project=<slug>`
and read-only clients using `/api/projects` continue to resolve; newly generated
links and the MCP surface use journey terminology. Restart Codex after installing
this version so its MCP tool catalog is refreshed.

## Test

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

