param(
    [string]$Version = "",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ReleaseRoot = Join-Path $ProjectRoot "releases"
$DistRoot = Join-Path $ProjectRoot "dist"

if (-not $Version) {
    $Source = Join-Path $ProjectRoot "rag_pbo_builder_gui.py"
    $VersionLine = Select-String -LiteralPath $Source -Pattern 'APP_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $VersionLine -or $VersionLine.Line -notmatch 'APP_VERSION\s*=\s*"([^"]+)"') {
        throw "Could not detect APP_VERSION from $Source. Pass -Version explicitly."
    }

    $Version = $Matches[1]
}

$SafeVersion = $Version -replace '[^A-Za-z0-9._-]+', '_'
$PackageDir = Join-Path $ReleaseRoot "RaG_PBO_Tools_$SafeVersion"
$ZipPath = Join-Path $ReleaseRoot "RaG_PBO_Tools_$SafeVersion.zip"
$ChecksumPath = Join-Path $PackageDir "SHA256SUMS.txt"

if (-not $SkipBuild) {
    & (Join-Path $ProjectRoot "build_rag_pbo_builder.ps1")
    & (Join-Path $ProjectRoot "build_rag_pbo_inspector.ps1")
}

$BuilderExe = Join-Path $DistRoot "RaG_PBO_Builder.exe"
$InspectorExe = Join-Path $DistRoot "RaG_PBO_Inspector.exe"

foreach ($Path in @($BuilderExe, $InspectorExe)) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Required release binary not found: $Path"
    }
}

if (Test-Path -LiteralPath $PackageDir) {
    Remove-Item -LiteralPath $PackageDir -Recurse -Force
}

if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}

New-Item -ItemType Directory -Path $PackageDir | Out-Null

Copy-Item -LiteralPath $BuilderExe -Destination $PackageDir
Copy-Item -LiteralPath $InspectorExe -Destination $PackageDir

foreach ($OptionalFile in @("README.md", "LICENSE.txt", "CHANGELOG.md")) {
    $Path = Join-Path $ProjectRoot $OptionalFile
    if (Test-Path -LiteralPath $Path) {
        Copy-Item -LiteralPath $Path -Destination $PackageDir
    }
}

$HashLines = foreach ($File in Get-ChildItem -LiteralPath $PackageDir -File | Sort-Object Name) {
    $Hash = Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256
    "$($Hash.Hash.ToLowerInvariant())  $($File.Name)"
}

$HashLines | Set-Content -LiteralPath $ChecksumPath -Encoding ASCII

Compress-Archive -Path (Join-Path $PackageDir "*") -DestinationPath $ZipPath -Force

if (-not (Test-Path -LiteralPath $ZipPath)) {
    throw "Release zip was not created: $ZipPath"
}

Write-Host "Release package: $ZipPath"
Write-Host "Release folder:  $PackageDir"
