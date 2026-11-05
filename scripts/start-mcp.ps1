[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'

foreach ($variableName in @('WANDER_HIPPO_URL', 'WANDER_HIPPO_TOKEN')) {
    if (-not (Get-Item -LiteralPath "Env:$variableName" -ErrorAction SilentlyContinue)) {
        $userValue = [Environment]::GetEnvironmentVariable($variableName, 'User')
        if ($userValue) {
            Set-Item -LiteralPath "Env:$variableName" -Value $userValue
        }
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Wander is not installed. Run scripts\setup.ps1 first.'
}

& $python -m wander.mcp_server
exit $LASTEXITCODE

