[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Wander is not installed. Run scripts\setup.ps1 first.'
}

if (-not $env:WANDER_DB) {
    $env:WANDER_DB = Join-Path $repoRoot 'data\wander.db'
}

& $python -m wander.mcp_server
exit $LASTEXITCODE

