[CmdletBinding()]
param(
    [switch]$PlanOnly,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$setupScript = Join-Path $PSScriptRoot 'setup.ps1'
$startMcpScript = Join-Path $PSScriptRoot 'start-mcp.ps1'
$sourceSkill = Join-Path $repoRoot 'skills\wander'
$codexHome = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex'
$destinationSkill = Join-Path $codexHome 'skills\wander'

$pythonCommand = Get-Command python -ErrorAction Stop
$codexCommand = Get-Command codex -ErrorAction Stop

Write-Host 'Wander Codex installation plan:'
Write-Host "  Repository: $repoRoot"
Write-Host "  Python:     $($pythonCommand.Source)"
Write-Host "  MCP entry:  $startMcpScript"
Write-Host "  Skill:      $destinationSkill"

if ($PlanOnly) {
    Write-Host 'Plan only: no files or Codex configuration were changed.'
}

if (-not $PlanOnly) {
    & $setupScript
    if ($LASTEXITCODE -ne 0) {
        throw "Wander environment setup failed with exit code $LASTEXITCODE."
    }

    New-Item -ItemType Directory -Path $destinationSkill -Force | Out-Null
    Get-ChildItem -LiteralPath $sourceSkill -Force | Copy-Item -Destination $destinationSkill -Recurse -Force
    if (-not (Test-Path -LiteralPath (Join-Path $destinationSkill 'SKILL.md'))) {
        throw "Wander skill was not installed at $destinationSkill."
    }

    $currentRegistration = & $codexCommand.Source mcp get wander 2>&1
    $registrationExists = $LASTEXITCODE -eq 0
    $registrationText = $currentRegistration -join [Environment]::NewLine
    $registrationMatches = $registrationExists -and $registrationText.Contains($startMcpScript)

    if ($Force -or -not $registrationMatches) {
        if ($registrationExists) {
            & $codexCommand.Source mcp remove wander
            if ($LASTEXITCODE -ne 0) {
                throw "Unable to remove the existing Wander MCP registration."
            }
        }
        & $codexCommand.Source mcp add wander -- powershell -NoProfile -ExecutionPolicy Bypass -File $startMcpScript
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to register the Wander MCP server."
        }
    } else {
        Write-Host 'Wander MCP registration already points at this checkout.'
    }

    & $codexCommand.Source mcp get wander
    if ($LASTEXITCODE -ne 0) {
        throw "Wander MCP registration verification failed."
    }

    Write-Host ''
    Write-Host 'Wander is installed for Codex.'
    Write-Host 'Restart Codex so the MCP server and skill are loaded.'
    Write-Host "After restart, run: & '$PSScriptRoot\start-web.ps1'"
    Write-Host 'Then ask Codex: Make a Wander out of this task.'
}
