[CmdletBinding()]
param(
    [string]$HostAddress = '0.0.0.0',
    [int]$Port = 8765,
    [string]$ShareBaseUrl = $env:WANDER_SHARE_BASE_URL
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Wander is not installed. Run scripts\setup.ps1 first.'
}

$database = Join-Path $repoRoot 'data\wander.db'
$arguments = @('-m', 'wander.web', '--host', $HostAddress, '--port', $Port, '--database', $database)
if ($ShareBaseUrl) {
    $arguments += @('--share-base-url', $ShareBaseUrl)
}
& $python @arguments
exit $LASTEXITCODE

