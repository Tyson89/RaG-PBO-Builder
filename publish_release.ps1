param(
    [string]$Remote = "origin",
    [string]$Branch = "main",
    [switch]$SkipPackage,
    [switch]$SkipTests,
    [switch]$ForceTag
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)

    & git @Args
    if ($LASTEXITCODE -ne 0) {
        throw "Git command failed: git $($Args -join ' ')"
    }
}

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

function Get-RepoUrl {
    param([string]$RemoteName)

    $Url = (& git remote get-url $RemoteName 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $Url) {
        return ""
    }

    $Url = ($Url | Select-Object -First 1).Trim()

    if ($Url -match "^git@github\.com:(.+?)\.git$") {
        return "https://github.com/$($Matches[1])"
    }

    if ($Url -match "^https://github\.com/(.+?)(\.git)?$") {
        return "https://github.com/$($Matches[1] -replace '\.git$', '')"
    }

    return ""
}

function Get-ReleasePackagePath {
    param([string]$Version)

    $SafeVersion = $Version -replace "[^A-Za-z0-9._-]+", "_"
    return Join-Path (Join-Path $ProjectRoot "releases") "RaG_PBO_Tools_$SafeVersion.zip"
}

function Write-ReleasePackageSummary {
    param([string]$ZipPath)

    $BuilderExe = Join-Path (Join-Path $ProjectRoot "dist") "RaG_PBO_Builder.exe"
    $InspectorExe = Join-Path (Join-Path $ProjectRoot "dist") "RaG_PBO_Inspector.exe"

    Write-Host ""
    Write-Host "Local build outputs:"

    foreach ($Path in @($BuilderExe, $InspectorExe, $ZipPath)) {
        if (-not (Test-Path -LiteralPath $Path)) {
            throw "Expected release output missing: $Path"
        }

        $Item = Get-Item -LiteralPath $Path
        $Hash = Get-FileHash -LiteralPath $Path -Algorithm SHA256
        Write-Host "  $($Item.FullName)"
        Write-Host "    Size:    $($Item.Length) bytes"
        Write-Host "    Updated: $($Item.LastWriteTime)"
        Write-Host "    SHA256:  $($Hash.Hash.ToLowerInvariant())"
    }
}

function Invoke-ReleaseTests {
    $PytestTemp = Join-Path $ProjectRoot ".pytest_publish_tmp"

    if (Test-Path -LiteralPath $PytestTemp) {
        Remove-Item -LiteralPath $PytestTemp -Recurse -Force
    }

    New-Item -ItemType Directory -Force -Path $PytestTemp | Out-Null

    try {
        python -m pytest -p no:cacheprovider --basetemp $PytestTemp
        if ($LASTEXITCODE -ne 0) {
            throw "Tests failed. Release was not published."
        }
    } finally {
        Remove-Item -LiteralPath $PytestTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Push-Location $ProjectRoot
try {
    $Version = Get-AppVersion
    $VersionToken = ConvertTo-VersionToken $Version
    $Tag = "v$VersionToken"
    $RepoUrl = Get-RepoUrl $Remote
    $ZipPath = Get-ReleasePackagePath $Version

    Write-Host "RaG PBO Tools release publish"
    Write-Host "Version: $Version"
    Write-Host "Tag:     $Tag"
    Write-Host "Remote:  $Remote"
    Write-Host "Branch:  $Branch"
    Write-Host ""

    Invoke-Git rev-parse --is-inside-work-tree | Out-Null

    $CurrentBranch = (& git branch --show-current).Trim()
    if ($CurrentBranch -ne $Branch) {
        throw "Current branch is '$CurrentBranch', expected '$Branch'. Switch branches or pass -Branch '$CurrentBranch'."
    }

    $Status = (& git status --porcelain)
    if ($Status) {
        throw "Working tree is not clean. Commit or stash changes before publishing."
    }

    if (-not $SkipTests) {
        Write-Host "Running tests..."
        Invoke-ReleaseTests
    }

    if (-not $SkipPackage) {
        Write-Host "Building local release package..."
        & (Join-Path $ProjectRoot "package_release.ps1")
    }

    Write-ReleasePackageSummary $ZipPath

    Write-Host "Checking release readiness..."
    & (Join-Path $ProjectRoot "check_release_ready.ps1") -SkipPackage

    $HeadSha = (& git rev-parse --short HEAD).Trim()
    Write-Host ""
    Write-Host "Publishing commit: $HeadSha"

    $LocalTagExists = $false
    & git rev-parse -q --verify "refs/tags/$Tag" *> $null
    if ($LASTEXITCODE -eq 0) {
        $LocalTagExists = $true
    }

    $RemoteTagExists = $false
    & git ls-remote --exit-code --tags $Remote "refs/tags/$Tag" *> $null
    if ($LASTEXITCODE -eq 0) {
        $RemoteTagExists = $true
    }

    if ($RemoteTagExists -and -not $ForceTag) {
        throw "Remote tag '$Tag' already exists. Use a new version number, or rerun with -ForceTag if you intentionally want to move it."
    }

    if ($LocalTagExists) {
        if (-not $ForceTag) {
            throw "Local tag '$Tag' already exists. Use a new version number, or rerun with -ForceTag if you intentionally want to move it."
        }

        Write-Host "Replacing local tag: $Tag"
        Invoke-Git tag -f $Tag
    } else {
        Write-Host "Creating local tag: $Tag"
        Invoke-Git tag $Tag
    }

    Write-Host "Pushing branch..."
    Invoke-Git push $Remote $Branch

    Write-Host "Pushing release tag..."
    if ($ForceTag) {
        Invoke-Git push --force $Remote $Tag
    } else {
        Invoke-Git push $Remote $Tag
    }

    Write-Host ""
    Write-Host "Release publish triggered."
    Write-Host "GitHub will build and upload its own release zip from commit $HeadSha."
    Write-Host "The local zip above is only a verification package unless you upload it manually."
    if ($RepoUrl) {
        Write-Host "Actions:  $RepoUrl/actions"
        Write-Host "Release:  $RepoUrl/releases/tag/$Tag"
    } else {
        Write-Host "Open GitHub Actions and wait for the Build Release workflow to finish."
    }
} finally {
    Pop-Location
}
