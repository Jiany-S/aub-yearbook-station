param([Parameter(Mandatory = $true)][string]$AppDirectory)
$ErrorActionPreference = "Stop"
$internalPath = Join-Path (Resolve-Path -LiteralPath $AppDirectory).Path '_internal'
if (-not (Test-Path -LiteralPath $internalPath -PathType Container)) { throw "Built application's _internal directory is missing" }
# Python and Qt can ship different MSVC runtime versions. The bootloader loads
# root DLLs first, so an older Python copy must not shadow Qt's newer runtime.
$runtimeGroups = Get-ChildItem -LiteralPath $internalPath -Recurse -File | Where-Object {
    $_.Name -match '^(VCRUNTIME140|MSVCP140)(_[A-Za-z0-9_]+)?\.dll$'
} | Group-Object Name
foreach ($runtimeGroup in $runtimeGroups) {
    $newest = $runtimeGroup.Group | Sort-Object {
        [version]("{0}.{1}.{2}.{3}" -f $_.VersionInfo.FileMajorPart,$_.VersionInfo.FileMinorPart,$_.VersionInfo.FileBuildPart,$_.VersionInfo.FilePrivatePart)
    } -Descending | Select-Object -First 1
    $destination = Join-Path $internalPath $runtimeGroup.Name
    if ($newest.FullName -ne $destination) { Copy-Item -LiteralPath $newest.FullName -Destination $destination -Force }
    Write-Output "Windows runtime $($runtimeGroup.Name): $($newest.VersionInfo.FileVersion)"
}
