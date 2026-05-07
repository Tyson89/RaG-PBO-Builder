$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Source = Join-Path $ProjectRoot "rag_pbo_builder_gui.py"
$Icon = Join-Path $ProjectRoot "assets\HEADONLY_SQUARE_2k.ico"

if (-not (Test-Path -LiteralPath $Source)) {
    throw "Source file not found: $Source"
}

if (-not (Test-Path -LiteralPath $Icon)) {
    throw "Icon file not found: $Icon"
}

python -m PyInstaller --clean --onefile --console `
    --icon "$Icon" `
    --add-data "$Icon;assets" `
    --name RaG_PBO_Builder `
    "$Source"

$Exe = Join-Path $ProjectRoot "dist\RaG_PBO_Builder.exe"
Write-Host "Built: $Exe"
