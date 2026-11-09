[CmdletBinding()]
param(
    [string]$OutputRoot,
    [switch]$AllowDirty,
    [switch]$SkipSelfTest
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $repoRoot 'artifacts'
}
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & (Join-Path $PSScriptRoot 'setup.ps1')
    if ($LASTEXITCODE -ne 0) {
        throw 'Wander environment setup failed.'
    }
}

& $python -m pip install --editable "${repoRoot}[packaging]"
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to install the pinned Wander packaging tools.'
}

$appVersion = (& $python -c 'import wander; print(wander.__version__)').Trim()
$gitSha = (& git -C $repoRoot rev-parse HEAD).Trim().ToLowerInvariant()
if ($LASTEXITCODE -ne 0 -or $gitSha -notmatch '^[0-9a-f]{40}$') {
    throw 'Could not resolve the Wander Git commit identity.'
}
$dirtyLines = @(& git -C $repoRoot status --porcelain --untracked-files=all)
$isDirty = $dirtyLines.Count -gt 0
if ($isDirty -and -not $AllowDirty) {
    throw 'Refusing to package a dirty checkout. Commit the batch or pass -AllowDirty for a development bundle.'
}
$versionId = "$appVersion+$($gitSha.Substring(0, 8))"
if ($isDirty) {
    $versionId += '.dirty'
}

$bundleParent = Join-Path $OutputRoot "bundles\$versionId"
$bundlePath = Join-Path $bundleParent 'Wander'
$workPath = Join-Path $OutputRoot "work\$versionId"
foreach ($path in @($bundleParent, $workPath)) {
    $resolved = [IO.Path]::GetFullPath($path)
    $prefix = $OutputRoot.TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a build path outside the output root: $resolved"
    }
    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
New-Item -ItemType Directory -Path $bundleParent -Force | Out-Null
New-Item -ItemType Directory -Path $workPath -Force | Out-Null

$spec = Join-Path $repoRoot 'packaging\Wander.spec'
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $bundleParent `
    --workpath $workPath `
    $spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

$manifestArguments = @(
    '-m', 'wander.release', 'create',
    '--bundle', $bundlePath,
    '--app-version', $appVersion,
    '--git-sha', $gitSha,
    '--entry-point', 'Wander.exe'
)
if ($isDirty) {
    $manifestArguments += '--dirty'
}
& $python @manifestArguments | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to create the Wander bundle manifest.'
}
& $python -m wander.release verify --bundle $bundlePath | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'The completed Wander bundle failed manifest verification.'
}

$selfTestOutput = Join-Path $bundleParent 'self-test.json'
if (-not $SkipSelfTest) {
    $executable = Join-Path $bundlePath 'Wander.exe'
    $process = Start-Process `
        -FilePath $executable `
        -ArgumentList @('--self-test', '--diagnostic-output', "`"$selfTestOutput`"") `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    if ($process.ExitCode -ne 0) {
        $diagnostic = if (Test-Path -LiteralPath $selfTestOutput) {
            Get-Content -Raw -LiteralPath $selfTestOutput
        } else {
            'No diagnostic report was produced.'
        }
        throw "The packaged Wander self-test failed with exit code $($process.ExitCode). $diagnostic"
    }
    $selfTest = Get-Content -Raw -LiteralPath $selfTestOutput | ConvertFrom-Json
    if ($selfTest.status -ne 'ok' -or -not $selfTest.endpoints_closed) {
        throw 'The packaged Wander self-test did not report a clean lifecycle.'
    }
}

[pscustomobject]@{
    version_id = $versionId
    bundle = $bundlePath
    manifest = (Join-Path $bundlePath 'wander-build.json')
    self_test = if ($SkipSelfTest) { $null } else { $selfTestOutput }
    git_dirty = $isDirty
} | ConvertTo-Json -Depth 3
