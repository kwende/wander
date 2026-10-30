[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $repoRoot '.venv'
$python = Join-Path $venvPath 'Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    python -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment at $venvPath"
    }
}

& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to update pip.'
}

& $python -m pip install --editable $repoRoot
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to install Wander.'
}

Write-Host "Wander environment ready: $python"

