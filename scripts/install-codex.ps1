[CmdletBinding()]
param(
    [switch]$PlanOnly,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$startMcpScript = Join-Path $PSScriptRoot 'start-mcp.ps1'
$sourceSkill = Join-Path $repoRoot 'skills\wander'
$codexHome = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex'
$destinationSkill = Join-Path $codexHome 'skills\wander'
$installState = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Programs\Wander\install.json'
$mcpUrl = 'http://127.0.0.1:8766/mcp'

$codexCommand = Get-Command codex -ErrorAction Stop

Write-Host 'Wander Codex installation plan:'
Write-Host "  Repository: $repoRoot"
Write-Host "  Desktop:    $installState"
Write-Host "  MCP URL:    $mcpUrl"
Write-Host "  Skill:      $destinationSkill"

if ($PlanOnly) {
    Write-Host 'Plan only: no files or Codex configuration were changed.'
}

if (-not $PlanOnly) {
    if (-not (Test-Path -LiteralPath $installState -PathType Leaf)) {
        throw 'Wander desktop is not installed. Build and install the desktop application first.'
    }

    New-Item -ItemType Directory -Path $destinationSkill -Force | Out-Null
    Get-ChildItem -LiteralPath $sourceSkill -Force | Copy-Item -Destination $destinationSkill -Recurse -Force
    if (-not (Test-Path -LiteralPath (Join-Path $destinationSkill 'SKILL.md'))) {
        throw "Wander skill was not installed at $destinationSkill."
    }

    $currentRegistration = & $codexCommand.Source mcp get wander 2>&1
    $registrationExists = $LASTEXITCODE -eq 0
    $registrationText = $currentRegistration -join [Environment]::NewLine
    $registrationMatches = $registrationExists `
        -and $registrationText.Contains('transport: streamable_http') `
        -and $registrationText.Contains($mcpUrl)
    $legacyRegistration = $registrationExists -and $registrationText.Contains($startMcpScript)

    if ($registrationExists -and -not $registrationMatches -and -not $legacyRegistration) {
        throw 'An unrecognized Wander MCP registration already exists. Remove it explicitly before installing.'
    }

    if ($Force -or $legacyRegistration -or -not $registrationExists) {
        if ($registrationExists) {
            & $codexCommand.Source mcp remove wander
            if ($LASTEXITCODE -ne 0) {
                throw "Unable to remove the existing Wander MCP registration."
            }
        }
        & $codexCommand.Source mcp add wander --url $mcpUrl
        if ($LASTEXITCODE -ne 0) {
            if ($legacyRegistration) {
                & $codexCommand.Source mcp add wander -- powershell -NoProfile -ExecutionPolicy Bypass -File $startMcpScript
            }
            throw 'Unable to register the Wander desktop MCP endpoint.'
        }
    } else {
        Write-Host 'Wander MCP registration already points at the desktop application.'
    }

    $verifiedRegistration = & $codexCommand.Source mcp get wander 2>&1
    $verifiedText = $verifiedRegistration -join [Environment]::NewLine
    if ($LASTEXITCODE -ne 0 -or -not $verifiedText.Contains($mcpUrl)) {
        throw "Wander MCP registration verification failed."
    }
    $verifiedRegistration

    Write-Host ''
    Write-Host 'Wander is installed for Codex.'
    Write-Host 'Open Wander from the Desktop or Start Menu, then restart Codex.'
    Write-Host 'The MCP endpoint is available only while Wander is open.'
    Write-Host 'Then ask Codex: Make a Wander out of this task.'
}
