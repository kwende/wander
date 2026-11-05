# Wander

Wander is a durable, multi-thread workboard for Codex journeys. SQLite is the
authoritative state. Codex mutates that state through semantic MCP tools; the
network Journey dashboard is deliberately read-only. The same application has
a localhost-only modal for human-controlled Jaunts: independent lightweight
tasks with free-form notes and retained history.

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
ledger. It refuses to overwrite an existing destination.

An immutable version can be staged without publishing any launch link:

```powershell
& .\scripts\install-desktop.ps1 `
    -BundlePath .\artifacts\bundles\<version>\Wander `
    -StageOnly
```

A full install is data-gated. It requires the per-user ledger to exist first,
unless `-AllowNewLedger` explicitly authorizes a new empty ledger. The installer
verifies WebView2 and every manifest hash, copies into a versioned directory,
then transactionally replaces the Desktop and Start Menu launch links. Pending
pointer transactions are restored on the next installer or rollback run.

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

Each journey can expose ordered HTTP links beneath its description. Active or
blocked parent work items expand into a read-only child checklist so the current
acceptance criteria are visible; completed parents collapse that detail. A
completed item can also retain verified GitHub commit links as code provenance.
The shared-journey URL (`?journey=<slug>&shared=1`) hides journey selection and
the Share control while preserving the journey links and work state.

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

The repository's `.mcp.json` starts `scripts/start-mcp.ps1`. Restart Codex after
setting up the environment so the repository MCP registry is rediscovered.

## Codex integration

For a personal installation available from every checkout, use the idempotent
installer from any clone location:

```powershell
& .\scripts\install-codex.ps1
```

It creates or updates `.venv`, installs Wander, copies the user skill, registers
the global MCP server against the current checkout, and verifies the resulting
registration. Use `-PlanOnly` to inspect its resolved paths without changing
the machine and `-Force` to replace an existing Wander MCP registration.

Restart Codex afterward. A conversation can then say `Make a Wander out of our
task`; the skill discovers or creates the journey, optionally attaches a
supported chat deep link for navigation, and builds the high-level work-item
sequence. The link is ordinary Journey data, not MCP caller identity.

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

