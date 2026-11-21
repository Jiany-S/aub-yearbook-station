param([string]$Python = ".\.venv\Scripts\python.exe", [switch]$Installer)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& $Python tools/license_notices.py
if ($LASTEXITCODE -ne 0) { throw "License collection failed" }
& $Python -m PyInstaller --noconfirm --clean --windowed --onedir --name AUBYearbookStation --paths src --hidden-import xlrd --hidden-import openpyxl --collect-data tzdata --copy-metadata PySide6-Essentials --add-data "LICENSE:." --add-data "THIRD_PARTY_NOTICES.md:." --add-data "build/thirdparty:thirdparty" packaging/launch.py
if ($LASTEXITCODE -ne 0) { throw "Windows packaging failed" }
$appVersion = & $Python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
New-Item -ItemType Directory -Force -Path dist-assets | Out-Null
$portablePath = Join-Path $projectRoot "dist-assets\AUBYearbookStation-$appVersion-Windows-x64-portable.zip"
Compress-Archive -Path dist/AUBYearbookStation -DestinationPath $portablePath -Force
if ($Installer) {
    $compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1
    if (-not $compiler) { $compiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
    if (-not (Test-Path -LiteralPath $compiler)) { throw "Inno Setup 6 is required for the setup installer" }
    & $compiler "/DAppVersion=$appVersion" packaging/windows.iss
    if ($LASTEXITCODE -ne 0) { throw "Installer creation failed" }
}
Get-ChildItem -LiteralPath dist-assets -File | Where-Object Extension -In '.zip','.exe' | ForEach-Object {
    $fileHash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$fileHash  $($_.Name)"
} | Set-Content -LiteralPath dist-assets/SHA256SUMS.txt -Encoding ascii
