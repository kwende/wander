$ErrorActionPreference = 'Stop'

function Resolve-WanderChildPath {
    param([string]$Root, [string]$RelativePath)
    if ([IO.Path]::IsPathRooted($RelativePath)) {
        throw "Bundle path must be relative: $RelativePath"
    }
    $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $candidate = [IO.Path]::GetFullPath((Join-Path $rootPath ($RelativePath -replace '/', '\')))
    if (-not $candidate.StartsWith($rootPath + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Bundle path escapes its root: $RelativePath"
    }
    return $candidate
}

function Get-WanderSha256 {
    param([string]$Path)
    $stream = [IO.File]::OpenRead([IO.Path]::GetFullPath($Path))
    try {
        $sha256 = [Security.Cryptography.SHA256]::Create()
        try {
            return ([BitConverter]::ToString($sha256.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
        } finally {
            $sha256.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

function Read-WanderBundleManifest {
    param(
        [string]$BundlePath,
        [switch]$AllowDevelopmentBuild
    )
    $bundle = [IO.Path]::GetFullPath($BundlePath)
    $manifestPath = Join-Path $bundle 'wander-build.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "Wander bundle manifest is missing: $manifestPath"
    }
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    if ($manifest.format_version -ne 1) {
        throw "Unsupported Wander bundle manifest format: $($manifest.format_version)"
    }
    if ($manifest.version_id -notmatch '^[0-9A-Za-z][0-9A-Za-z.+-]*$') {
        throw "Unsafe Wander version identity: $($manifest.version_id)"
    }
    if ($manifest.git_dirty -and -not $AllowDevelopmentBuild) {
        throw 'Refusing to install a dirty development bundle.'
    }
    $entryPoint = Resolve-WanderChildPath $bundle $manifest.entry_point
    if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf)) {
        throw "Wander bundle entry point is missing: $entryPoint"
    }

    $expected = @{}
    foreach ($file in @($manifest.files)) {
        if (-not $file.path -or $expected.ContainsKey([string]$file.path)) {
            throw 'Wander bundle manifest contains a missing or duplicate file path.'
        }
        $expected[[string]$file.path] = $file
    }
    $actual = @{}
    foreach ($file in Get-ChildItem -LiteralPath $bundle -File -Recurse -Force) {
        if ($file.FullName -eq $manifestPath) {
            continue
        }
        $relative = $file.FullName.Substring($bundle.TrimEnd('\').Length + 1).Replace('\', '/')
        $actual[$relative] = $file
    }
    if ($expected.Count -ne $actual.Count) {
        throw 'Wander bundle file set does not match its manifest.'
    }
    foreach ($relative in $actual.Keys) {
        if (-not $expected.ContainsKey($relative)) {
            throw "Unexpected file in Wander bundle: $relative"
        }
        $file = $actual[$relative]
        $record = $expected[$relative]
        if ($file.Length -ne [int64]$record.size -or (Get-WanderSha256 $file.FullName) -ne $record.sha256) {
            throw "Wander bundle file failed verification: $relative"
        }
    }
    return [pscustomobject]@{
        Bundle = $bundle
        ManifestPath = $manifestPath
        ManifestHash = Get-WanderSha256 $manifestPath
        Manifest = $manifest
        EntryPoint = $entryPoint
    }
}

function Get-WanderWebView2Version {
    $client = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    $locations = @(
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$client",
        "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$client",
        "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$client"
    )
    foreach ($location in $locations) {
        $version = (Get-ItemProperty -LiteralPath $location -Name pv -ErrorAction SilentlyContinue).pv
        if ($version -and $version -ne '0.0.0.0') {
            return [string]$version
        }
    }
    throw 'Microsoft Edge WebView2 Evergreen Runtime is required but was not detected.'
}

function Write-WanderJsonAtomic {
    param([string]$Path, [object]$Value)
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $temporary = Join-Path $parent ".$([IO.Path]::GetFileName($Path)).$([guid]::NewGuid().ToString('N')).tmp"
    try {
        $Value | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $temporary -Encoding UTF8
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function New-WanderShortcutTemporary {
    param(
        [string]$DestinationPath,
        [string]$TargetPath,
        [string]$Arguments,
        [string]$WorkingDirectory
    )
    $parent = Split-Path -Parent $DestinationPath
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $temporary = Join-Path $parent ".Wander.$([guid]::NewGuid().ToString('N')).lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($temporary)
    $shortcut.TargetPath = $TargetPath
    $shortcut.Arguments = $Arguments
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.IconLocation = "$TargetPath,0"
    $shortcut.Description = 'Open Wander'
    $shortcut.Save()
    return $temporary
}

function Start-WanderPointerTransaction {
    param([string]$InstallRoot, [string[]]$Targets)
    $transactions = Join-Path $InstallRoot 'transactions'
    $root = Join-Path $transactions ([guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    $entries = @()
    for ($index = 0; $index -lt $Targets.Count; $index++) {
        $target = [IO.Path]::GetFullPath($Targets[$index])
        $backup = Join-Path $root "backup-$index"
        $exists = Test-Path -LiteralPath $target -PathType Leaf
        if ($exists) {
            Copy-Item -LiteralPath $target -Destination $backup -Force
        }
        $entries += [ordered]@{ target = $target; backup = $backup; existed = $exists }
    }
    Write-WanderJsonAtomic (Join-Path $root 'transaction.json') ([ordered]@{ entries = $entries })
    return $root
}

function Restore-WanderPointerTransaction {
    param([string]$TransactionRoot)
    $transactionPath = [IO.Path]::GetFullPath($TransactionRoot)
    if ([IO.Path]::GetFileName((Split-Path -Parent $transactionPath)) -ne 'transactions' -or
        [IO.Path]::GetFileName($transactionPath) -notmatch '^[0-9a-f]{32}$') {
        throw "Refusing to operate on an unsafe Wander transaction path: $transactionPath"
    }
    if (Test-Path -LiteralPath (Join-Path $transactionPath 'committed')) {
        Remove-Item -LiteralPath $transactionPath -Recurse -Force
        return
    }
    $journalPath = Join-Path $transactionPath 'transaction.json'
    if (-not (Test-Path -LiteralPath $journalPath -PathType Leaf)) {
        throw "Incomplete Wander installation transaction has no journal: $TransactionRoot"
    }
    $journal = Get-Content -Raw -LiteralPath $journalPath | ConvertFrom-Json
    foreach ($entry in @($journal.entries)) {
        if ($entry.existed) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $entry.target) -Force | Out-Null
            Copy-Item -LiteralPath $entry.backup -Destination $entry.target -Force
        } else {
            Remove-Item -LiteralPath $entry.target -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath $transactionPath -Recurse -Force
}

function Restore-WanderPointerTransactions {
    param([string]$InstallRoot)
    $transactions = Join-Path $InstallRoot 'transactions'
    if (-not (Test-Path -LiteralPath $transactions -PathType Container)) {
        return
    }
    foreach ($transaction in Get-ChildItem -LiteralPath $transactions -Directory -Force) {
        Restore-WanderPointerTransaction $transaction.FullName
    }
}

function Complete-WanderPointerTransaction {
    param([string]$TransactionRoot)
    $transactionPath = [IO.Path]::GetFullPath($TransactionRoot)
    if ([IO.Path]::GetFileName((Split-Path -Parent $transactionPath)) -ne 'transactions' -or
        [IO.Path]::GetFileName($transactionPath) -notmatch '^[0-9a-f]{32}$') {
        throw "Refusing to complete an unsafe Wander transaction path: $transactionPath"
    }
    New-Item -ItemType File -Path (Join-Path $transactionPath 'committed') -Force | Out-Null
    Remove-Item -LiteralPath $transactionPath -Recurse -Force
}

function Set-WanderLaunchPointers {
    param(
        [string]$InstallRoot,
        [string]$VersionPath,
        [object]$Manifest,
        [string]$DataRoot,
        [string]$DesktopDirectory,
        [string]$StartMenuDirectory,
        [string]$PreviousVersion
    )
    $entryPoint = Resolve-WanderChildPath $VersionPath $Manifest.entry_point
    $desktopShortcut = Join-Path $DesktopDirectory 'Wander.lnk'
    $startMenuShortcut = Join-Path $StartMenuDirectory 'Wander.lnk'
    $statePath = Join-Path $InstallRoot 'install.json'
    $database = Join-Path $DataRoot 'data\wander.db'
    $backups = Join-Path $DataRoot 'backups'
    $arguments = "--database `"$database`" --backups-directory `"$backups`""
    $transaction = Start-WanderPointerTransaction $InstallRoot @(
        $desktopShortcut,
        $startMenuShortcut,
        $statePath
    )
    $temporaryShortcuts = @()
    try {
        $temporaryShortcuts += New-WanderShortcutTemporary $desktopShortcut $entryPoint $arguments $VersionPath
        $temporaryShortcuts += New-WanderShortcutTemporary $startMenuShortcut $entryPoint $arguments $VersionPath
        Move-Item -LiteralPath $temporaryShortcuts[0] -Destination $desktopShortcut -Force
        Move-Item -LiteralPath $temporaryShortcuts[1] -Destination $startMenuShortcut -Force
        $state = [ordered]@{
            format_version = 1
            active_version = [string]$Manifest.version_id
            previous_version = $PreviousVersion
            manifest_sha256 = Get-WanderSha256 (Join-Path $VersionPath 'wander-build.json')
            entry_point = [string]$Manifest.entry_point
            install_root = [IO.Path]::GetFullPath($InstallRoot)
            data_root = [IO.Path]::GetFullPath($DataRoot)
            desktop_shortcut = [IO.Path]::GetFullPath($desktopShortcut)
            start_menu_shortcut = [IO.Path]::GetFullPath($startMenuShortcut)
            supported_schema = [int]$Manifest.supported_schema
            updated_at = [DateTimeOffset]::UtcNow.ToString('o')
        }
        Write-WanderJsonAtomic $statePath $state
        Complete-WanderPointerTransaction $transaction
        return [pscustomobject]$state
    } catch {
        foreach ($temporary in $temporaryShortcuts) {
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $transaction) {
            Restore-WanderPointerTransaction $transaction
        }
        throw
    }
}
