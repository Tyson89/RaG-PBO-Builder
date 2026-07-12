param(
    [string]$InnoCompiler = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    [switch]$SkipExeBuild
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

Push-Location $ProjectRoot
try {
    if (-not $SkipExeBuild) {
        & (Join-Path $ProjectRoot "build_rag_pbo_builder.ps1")
        & (Join-Path $ProjectRoot "build_rag_pbo_inspector.ps1")
    }

    if (-not (Test-Path -LiteralPath $InnoCompiler -PathType Leaf)) {
        throw "Inno Setup compiler not found: $InnoCompiler"
    }

    $AppVersion = (python -c "from rag_version import APP_VERSION; print(APP_VERSION)").Trim()
    $VersionParts = @([regex]::Matches($AppVersion, '\d+') | ForEach-Object { $_.Value })
    while ($VersionParts.Count -lt 4) {
        $VersionParts += "0"
    }
    $NumericVersion = ($VersionParts[0..3] -join ".")

    & $InnoCompiler "/DAppVersion=$AppVersion" "/DAppVersionNumeric=$NumericVersion" "installer\RaG_PBO_Tools.iss"
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE"
    }

    $Installer = Get-Item "dist\installer\RaG_PBO_Tools_Setup.exe"
    $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Installer.FullName).Hash.ToLowerInvariant()
    $ChecksumPath = "$($Installer.FullName).sha256"
    "$Hash  $($Installer.Name)" | Set-Content -LiteralPath $ChecksumPath -Encoding ascii
    Write-Host "Built $($Installer.FullName)"
    Write-Host "Built $ChecksumPath"
}
finally {
    Pop-Location
}
