# Wander

Wander is a durable, multi-thread workboard for Codex projects. SQLite is the
authoritative state. Codex mutates that state through semantic MCP tools; the
network dashboard is deliberately read-only.

## Run locally

```powershell
Set-Location D:\repos\wander
& .\scripts\setup.ps1
& .\scripts\start-web.ps1
```

The dashboard listens on all network interfaces at port `8765`. Open
`http://localhost:8765` on this machine or `http://<host-ip>:8765` from another
machine on the same reachable network. Windows Firewall may require an inbound
rule. Anyone who can reach the port can read the project notes; no HTTP write
surface exists.

The repository's `.mcp.json` starts `scripts/start-mcp.ps1`. Restart Codex after
setting up the environment so the repository MCP registry is rediscovered.

## Codex integration

For a personal installation that is available from every checkout, register the
MCP server and copy the skill into the Codex user skill directory:

```powershell
codex mcp add wander -- powershell -NoProfile -ExecutionPolicy Bypass -File D:\repos\wander\scripts\start-mcp.ps1
Copy-Item -Recurse -Force .\skills\wander "$env:USERPROFILE\.codex\skills\wander"
```

Restart Codex after changing MCP or skill registrations. A conversation can
then say `Make a Wander out of our task`; the skill discovers or creates the
project, binds the MCP session to the host-provided conversation identity, and
builds the high-level work-item sequence. The identity is passed explicitly
because a globally launched MCP process does not necessarily inherit the
calling thread's environment.

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
Codex chats --semantic MCP tools--> SQLite <--GET only-- dashboard
```

Every mutation:

1. Requires the project version last read by the caller.
2. Runs in a SQLite `BEGIN IMMEDIATE` transaction.
3. Advances the project version.
4. Adds an append-only provenance event.

A stale chat receives a conflict instead of overwriting newer work. Work-item
and project completion require evidence.

Projects may designate one Codex thread as their center. A different thread can
still write, but every read or mutation result includes a prominent
`writer_warning` pointing back to the designated `codex://` conversation. This
is a workflow bumper for an experimental tool, not authentication or a security
boundary.

## Test

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

