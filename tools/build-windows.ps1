$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$packageName = "k5qref-windows-x64"
$releaseDir = Join-Path $projectRoot "release"
$packageDir = Join-Path $releaseDir $packageName
$zipPath = Join-Path $releaseDir "$packageName.zip"
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

$expectedPrefix = $projectRoot.TrimEnd("\") + "\"
if (-not $packageDir.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Package path escaped the project directory: $packageDir"
}

Set-Location $projectRoot

if (-not (Test-Path -LiteralPath $venvPython)) {
    python -m venv .venv
}
& $venvPython -m pip install -r requirements.txt
& $venvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --noconsole `
    --name k5qref `
    --icon "assets\k5qref.ico" `
    --add-data "assets;assets" `
    main.py

if (Test-Path -LiteralPath $packageDir) {
    Remove-Item -LiteralPath $packageDir -Recurse -Force
}
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

New-Item -ItemType Directory -Path $packageDir -Force | Out-Null
Copy-Item -LiteralPath "dist\k5qref.exe" -Destination $packageDir
Copy-Item -LiteralPath "config.json" -Destination $packageDir
Copy-Item -LiteralPath "README.md" -Destination $packageDir
Copy-Item -LiteralPath "LICENSE" -Destination $packageDir
Compress-Archive -LiteralPath $packageDir -DestinationPath $zipPath

Write-Host "Created $zipPath"
