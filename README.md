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

