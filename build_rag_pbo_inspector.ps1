$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Source = Join-Path $ProjectRoot "rag_pbo_inspector_gui.py"
$Icon = Join-Path $ProjectRoot "assets\HEADONLY_SQUARE_2k.ico"

if (-not (Test-Path -LiteralPath $Source)) {
    throw "Source file not found: $Source"
}

if (-not (Test-Path -LiteralPath $Icon)) {
    throw "Icon file not found: $Icon"
}

python -c "import PyInstaller, tkinterdnd2"
if ($LASTEXITCODE -ne 0) {
    throw "Missing build dependency. Run: python -m pip install -r requirements.txt"
}

python -m PyInstaller --clean --onefile --console `
    --icon "$Icon" `
    --add-data "$Icon;assets" `
    --collect-all tkinterdnd2 `
    --name RaG_PBO_Inspector `
    "$Source"

$Exe = Join-Path $ProjectRoot "dist\RaG_PBO_Inspector.exe"
Write-Host "Built: $Exe"
