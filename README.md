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

The dashboard listens on all network interfaces at port `8765`. Open
`http://localhost:8765` on this machine or `http://<host-ip>:8765` from another
machine on the same reachable network. Windows Firewall may require an inbound
rule. Anyone who can reach the port can read the Journey notes. Jaunt reads and
writes require a loopback TCP peer; LAN clients receive `403` even if they know
the Jaunt API path.

The server advertises its preferred LAN IPv4 address to the Share action, so a
link copied from `localhost` remains reachable from another machine. On a VPN or
multi-NIC host, override auto-detection explicitly:

```powershell
& .\scripts\start-web.ps1 -ShareBaseUrl 'http://172.17.5.65:8765'
```

`WANDER_SHARE_BASE_URL` provides the same override for unattended startup. This
is a LAN-reachable address, not discovery of an Internet/NAT public address.

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
task`; the skill discovers or creates the journey, binds the MCP session to the
host-provided conversation identity, and builds the high-level work-item
sequence. The identity is passed explicitly because a globally launched MCP
process does not necessarily inherit the calling thread's environment.

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
  "changed": {"work_item_ids": ["..."]},
  "writer_warning": ""
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

Journeys may designate one Codex thread as their center. A different thread can
still write, but every read or mutation result includes a prominent
`writer_warning` pointing back to the designated `codex://` conversation. This
is a workflow bumper for an experimental tool, not authentication or a security
boundary.

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

