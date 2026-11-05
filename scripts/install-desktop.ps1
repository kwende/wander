[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$BundlePath,
    [string]$InstallRoot,
    [string]$DataRoot,
    [string]$DesktopDirectory,
    [string]$StartMenuDirectory,
    [switch]$StageOnly,
    [switch]$PlanOnly,
    [switch]$AllowNewLedger,
    [switch]$AllowDevelopmentBuild,
    [switch]$SkipWebView2Check
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'desktop-install-common.ps1')

$localAppData = [Environment]::GetFolderPath('LocalApplicationData')
if (-not $InstallRoot) { $InstallRoot = Join-Path $localAppData 'Programs\Wander' }
if (-not $DataRoot) { $DataRoot = Join-Path $localAppData 'Wander' }
if (-not $DesktopDirectory) { $DesktopDirectory = [Environment]::GetFolderPath('Desktop') }
if (-not $StartMenuDirectory) { $StartMenuDirectory = [Environment]::GetFolderPath('Programs') }
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$DataRoot = [IO.Path]::GetFullPath($DataRoot)

if (-not $PlanOnly) {
    Restore-WanderPointerTransactions $InstallRoot
}
$bundle = Read-WanderBundleManifest $BundlePath -AllowDevelopmentBuild:$AllowDevelopmentBuild
$webViewVersion = if ($SkipWebView2Check) { 'check-skipped' } else { Get-WanderWebView2Version }
$versionPath = Join-Path $InstallRoot "versions\$($bundle.Manifest.version_id)"
$database = Join-Path $DataRoot 'data\wander.db'

if ($PlanOnly) {
    [pscustomobject]@{
        action = 'plan'
        version_id = $bundle.Manifest.version_id
        source = $bundle.Bundle
        destination = $versionPath
        database = $database
        stage_only = [bool]$StageOnly
        webview2 = $webViewVersion
    } | ConvertTo-Json -Depth 3
    return
}

if (-not $StageOnly -and -not $AllowNewLedger -and -not (Test-Path -LiteralPath $database -PathType Leaf)) {
    throw "No migrated Wander ledger exists at $database. Stage the bundle first or pass -AllowNewLedger explicitly."
}

$versionsRoot = Join-Path $InstallRoot 'versions'
New-Item -ItemType Directory -Path $versionsRoot -Force | Out-Null
if (Test-Path -LiteralPath $versionPath) {
    $installed = Read-WanderBundleManifest $versionPath -AllowDevelopmentBuild:$AllowDevelopmentBuild
    if ($installed.ManifestHash -ne $bundle.ManifestHash) {
        throw "Installed version $($bundle.Manifest.version_id) differs from the supplied immutable bundle."
    }
} else {
    $staging = Join-Path $versionsRoot ".$($bundle.Manifest.version_id).$([guid]::NewGuid().ToString('N')).staging"
    try {
        New-Item -ItemType Directory -Path $staging -Force | Out-Null
        Get-ChildItem -LiteralPath $bundle.Bundle -Force | Copy-Item -Destination $staging -Recurse -Force
        $staged = Read-WanderBundleManifest $staging -AllowDevelopmentBuild:$AllowDevelopmentBuild
        if ($staged.ManifestHash -ne $bundle.ManifestHash) {
            throw 'Staged Wander bundle differs from its source manifest.'
        }
        Move-Item -LiteralPath $staging -Destination $versionPath
    } finally {
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($StageOnly) {
    [pscustomobject]@{
        action = 'staged'
        version_id = $bundle.Manifest.version_id
        version_path = $versionPath
        webview2 = $webViewVersion
    } | ConvertTo-Json -Depth 3
    return
}

$statePath = Join-Path $InstallRoot 'install.json'
$previousVersion = $null
if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    $current = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
    $previousVersion = if ($current.active_version -eq $bundle.Manifest.version_id) {
        $current.previous_version
    } else {
        $current.active_version
    }
}
$state = Set-WanderLaunchPointers `
    $InstallRoot `
    $versionPath `
    $bundle.Manifest `
    $DataRoot `
    $DesktopDirectory `
    $StartMenuDirectory `
    $previousVersion

[pscustomobject]@{
    action = 'installed'
    version_id = $state.active_version
    previous_version = $state.previous_version
    version_path = $versionPath
    desktop_shortcut = $state.desktop_shortcut
    start_menu_shortcut = $state.start_menu_shortcut
    database = $database
    webview2 = $webViewVersion
} | ConvertTo-Json -Depth 3
