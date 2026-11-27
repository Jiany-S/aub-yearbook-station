param([string]$Python = ".\.venv\Scripts\python.exe", [switch]$Installer)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& $Python tools/license_notices.py
if ($LASTEXITCODE -ne 0) { throw "License collection failed" }
$pythonInfo = & $Python -c "import json,sys; print(json.dumps({'executable':sys.executable,'base':sys.base_prefix}))" | ConvertFrom-Json
$originalBuildPath = $env:PATH
try {
    # Restrict binary discovery to this Python installation and Windows. Other
    # tools may put incompatible ICU/UCRT DLLs on the developer's global PATH.
    $env:PATH = @((Split-Path -Parent $pythonInfo.executable),$pythonInfo.base,(Join-Path $pythonInfo.base 'DLLs'),"$env:SystemRoot\System32",$env:SystemRoot,"$env:SystemRoot\System32\Wbem","$env:SystemRoot\System32\WindowsPowerShell\v1.0") -join ';'
    & $pythonInfo.executable -m PyInstaller --noconfirm --clean --windowed --onedir --name AUBYearbookStation --paths src --hidden-import xlrd --hidden-import openpyxl --collect-data tzdata --copy-metadata PySide6-Essentials --add-data "LICENSE:." --add-data "THIRD_PARTY_NOTICES.md:." --add-data "build/thirdparty:thirdparty" --add-data "src/yearbook/assets:yearbook/assets" packaging/launch.py
    $freezeResult = $LASTEXITCODE
} finally {
    $env:PATH = $originalBuildPath
}
if ($freezeResult -ne 0) { throw "Windows packaging failed" }
$canonLibraries = Get-ChildItem -LiteralPath dist/AUBYearbookStation -Recurse -File | Where-Object { $_.Name -in 'EDSDK.dll','EdsImage.dll' }
if ($canonLibraries) { throw "Canon SDK binaries must remain external to the public release" }
& "$PSScriptRoot/align_msvc.ps1" -AppDirectory dist/AUBYearbookStation
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
