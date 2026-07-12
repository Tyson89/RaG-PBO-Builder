param(
    [string]$Version = "",
    [string]$InnoCompiler = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$DistRoot = Join-Path $ProjectRoot "dist"
$InstallerPath = Join-Path $DistRoot "installer\RaG_PBO_Tools_Setup.exe"
$InstallerChecksumPath = "$InstallerPath.sha256"

if (-not $Version) {
    $VersionOutput = python -c "from rag_version import APP_VERSION; print(APP_VERSION)"
    if ($LASTEXITCODE -ne 0 -or -not $VersionOutput) {
        throw "Could not detect APP_VERSION from rag_version.py. Pass -Version explicitly."
    }

    $Version = $VersionOutput.Trim()
}

if (-not $SkipBuild) {
    & (Join-Path $ProjectRoot "build_rag_pbo_tools_installer.ps1") -InnoCompiler $InnoCompiler
}

foreach ($Path in @($InstallerPath, $InstallerChecksumPath)) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Required installer output missing: $Path"
    }
}

Write-Host "Release installer: $InstallerPath"
Write-Host "Installer hash:    $InstallerChecksumPath"
