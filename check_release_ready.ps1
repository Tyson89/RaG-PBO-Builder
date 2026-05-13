param(
    [switch]$SkipPackage
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ReleaseRoot = Join-Path $ProjectRoot "releases"
$DistRoot = Join-Path $ProjectRoot "dist"

function Get-AppVersion {
    Push-Location $ProjectRoot
    try {
        $Output = python -c "from rag_version import APP_VERSION; print(APP_VERSION)"
        if ($LASTEXITCODE -ne 0) {
            throw "Python failed while reading rag_version.APP_VERSION."
        }

        $Version = ($Output -join "`n").Trim()
        if (-not $Version) {
            throw "rag_version.APP_VERSION is empty."
        }

        return $Version
    } finally {
        Pop-Location
    }
}

function ConvertTo-VersionToken {
    param([string]$Value)

    $Token = $Value.Trim().ToLowerInvariant()

    if ($Token.StartsWith("v")) {
        $Token = $Token.Substring(1)
    }

    $Token = $Token -replace "\s+", "-"
    $Token = $Token -replace "_+", "-"
    $Token = $Token -replace "[^a-z0-9.\-]+", ""
    $Token = $Token -replace "-+", "-"
    return $Token.Trim("-")
}

function Get-CurrentGitTag {
    if ($env:GITHUB_REF_TYPE -eq "tag" -and $env:GITHUB_REF_NAME) {
        return $env:GITHUB_REF_NAME
    }

    if ($env:GITHUB_REF -and $env:GITHUB_REF.StartsWith("refs/tags/")) {
        return $env:GITHUB_REF.Substring("refs/tags/".Length)
    }

    Push-Location $ProjectRoot
    try {
        $Tags = & git tag --points-at HEAD
        if ($LASTEXITCODE -eq 0 -and $Tags) {
            return ($Tags | Select-Object -First 1).Trim()
        }
    } finally {
        Pop-Location
    }

    return ""
}

function Assert-FileContains {
    param(
        [string]$Path,
        [string]$Needle,
        [string]$Message
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Required file missing: $Path"
    }

    $Content = Get-Content -LiteralPath $Path -Raw -Encoding UTF8

    if (-not $Content.Contains($Needle)) {
        throw $Message
    }
}

$Version = Get-AppVersion
$SafeVersion = $Version -replace "[^A-Za-z0-9._-]+", "_"
$ZipPath = Join-Path $ReleaseRoot "RaG_PBO_Tools_$SafeVersion.zip"

Write-Host "Release readiness check"
Write-Host "Version: $Version"

Assert-FileContains `
    -Path (Join-Path $ProjectRoot "README.md") `
    -Needle $Version `
    -Message "README.md does not contain version '$Version'."

$Changelog = Join-Path $ProjectRoot "CHANGELOG.md"
if (-not (Test-Path -LiteralPath $Changelog)) {
    throw "Required file missing: $Changelog"
}

$ChangelogContent = Get-Content -LiteralPath $Changelog -Raw -Encoding UTF8
$HeadingPattern = "(?m)^##\s+$([regex]::Escape($Version))\s*$"
if ($ChangelogContent -notmatch $HeadingPattern) {
    throw "CHANGELOG.md has no heading for version '$Version'."
}

$CurrentTag = Get-CurrentGitTag
if ($CurrentTag) {
    $VersionToken = ConvertTo-VersionToken $Version
    $TagToken = ConvertTo-VersionToken $CurrentTag

    if ($TagToken -ne $VersionToken) {
        throw "Git tag '$CurrentTag' does not match app version '$Version'. Expected tag like 'v$VersionToken'."
    }

    Write-Host "Git tag matches version: $CurrentTag"
} else {
    Write-Host "No exact git tag detected; skipping tag/version comparison."
}

if (-not $SkipPackage) {
    & (Join-Path $ProjectRoot "package_release.ps1") -SkipBuild
}

$RequiredPaths = @(
    (Join-Path $DistRoot "RaG_PBO_Builder.exe"),
    (Join-Path $DistRoot "RaG_PBO_Inspector.exe"),
    $ZipPath
)

foreach ($Path in $RequiredPaths) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Required release output missing: $Path"
    }
}

$ExtractRoot = $env:RUNNER_TEMP
if (-not $ExtractRoot) {
    $ExtractRoot = [System.IO.Path]::GetTempPath()
}

$ExtractDir = Join-Path $ExtractRoot "rag-release-ready-check"

if (Test-Path -LiteralPath $ExtractDir) {
    Remove-Item -LiteralPath $ExtractDir -Recurse -Force
}

New-Item -ItemType Directory -Path $ExtractDir | Out-Null
Expand-Archive -LiteralPath $ZipPath -DestinationPath $ExtractDir -Force

foreach ($Name in @("RaG_PBO_Builder.exe", "RaG_PBO_Inspector.exe", "SHA256SUMS.txt")) {
    $Path = Join-Path $ExtractDir $Name
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Release zip is missing: $Name"
    }
}

$ChecksumContent = Get-Content -LiteralPath (Join-Path $ExtractDir "SHA256SUMS.txt") -Raw -Encoding ASCII
foreach ($Name in @("RaG_PBO_Builder.exe", "RaG_PBO_Inspector.exe")) {
    if (-not $ChecksumContent.Contains($Name)) {
        throw "SHA256SUMS.txt does not include $Name."
    }
}

Write-Host "Release readiness check passed: $ZipPath"
