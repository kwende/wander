[CmdletBinding()]
param(
    [string]$InstallRoot,
    [string]$DesktopDirectory,
    [string]$StartMenuDirectory,
    [string]$VersionId,
    [switch]$AllowDevelopmentBuild
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'desktop-install-common.ps1')

$localAppData = [Environment]::GetFolderPath('LocalApplicationData')
if (-not $InstallRoot) { $InstallRoot = Join-Path $localAppData 'Programs\Wander' }
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)

Restore-WanderPointerTransactions $InstallRoot
$statePath = Join-Path $InstallRoot 'install.json'
if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
    throw "Wander installation state does not exist: $statePath"
}
$current = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if (-not $DesktopDirectory) { $DesktopDirectory = Split-Path -Parent $current.desktop_shortcut }
if (-not $StartMenuDirectory) { $StartMenuDirectory = Split-Path -Parent $current.start_menu_shortcut }
$targetVersion = if ($VersionId) { $VersionId } else { $current.previous_version }
if (-not $targetVersion) {
    throw 'No previous Wander version is available for rollback.'
}
if ($targetVersion -eq $current.active_version) {
    throw "Wander version $targetVersion is already active."
}
$versionsRoot = Join-Path $InstallRoot 'versions'
$versionPath = Resolve-WanderChildPath $versionsRoot $targetVersion
$bundle = Read-WanderBundleManifest $versionPath -AllowDevelopmentBuild:$AllowDevelopmentBuild
if ($bundle.Manifest.version_id -ne $targetVersion) {
    throw "Rollback target manifest identity does not match its version directory: $targetVersion"
}
$database = Join-Path $current.data_root 'data\wander.db'

if (Test-Path -LiteralPath $database -PathType Leaf) {
    $diagnostic = Join-Path ([IO.Path]::GetTempPath()) "wander-rollback-$([guid]::NewGuid().ToString('N')).json"
    try {
        $process = Start-Process `
            -FilePath $bundle.EntryPoint `
            -ArgumentList @('--check-database', "`"$database`"", '--diagnostic-output', "`"$diagnostic`"") `
            -WindowStyle Hidden `
            -Wait `
            -PassThru
        $report = if (Test-Path -LiteralPath $diagnostic) {
            Get-Content -Raw -LiteralPath $diagnostic | ConvertFrom-Json
        } else {
            $null
        }
        if ($process.ExitCode -ne 0 -or -not $report -or -not $report.compatible) {
            $detail = if ($report) { $report | ConvertTo-Json -Compress } else { 'no diagnostic report' }
            throw "Wander rollback compatibility check failed: $detail"
        }
    } finally {
        Remove-Item -LiteralPath $diagnostic -Force -ErrorAction SilentlyContinue
    }
}

$state = Set-WanderLaunchPointers `
    $InstallRoot `
    $versionPath `
    $bundle.Manifest `
    $current.data_root `
    $DesktopDirectory `
    $StartMenuDirectory `
    $current.active_version

[pscustomobject]@{
    action = 'rolled-back'
    version_id = $state.active_version
    previous_version = $state.previous_version
    database = $database
    data_restored = $false
} | ConvertTo-Json -Depth 3
