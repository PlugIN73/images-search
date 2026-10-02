# Build "Poisk-kartinok" for Windows (folder + zip).
# Run from the project root in PowerShell:  powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
# Needs Python 3.11 in PATH (or set $env:PYTHON). Chromium is bundled next to the exe
# (dist\Poisk-kartinok\ms-playwright), nothing gets installed into the system.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$Name = "Poisk-kartinok"
$Version = (Select-String -Path poisk.py -Pattern '^ВЕРСИЯ = "(.+)"' -Encoding UTF8).Matches[0].Groups[1].Value
$Build = "build\windows"
$Zip = "dist\$Name-$Version-windows.zip"
$Python = if ($env:PYTHON) { $env:PYTHON } else { "python" }

Write-Host "== Version $Version"
Remove-Item -Recurse -Force $Build, dist -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $Build | Out-Null

Write-Host "== Python and libraries"
& $Python -m venv "$Build\venv"
$Py = "$Build\venv\Scripts\python.exe"
& $Py -m pip install --upgrade pip | Out-Null
& $Py -m pip install -r requirements.txt -r packaging\requirements-build.txt
if ($LASTEXITCODE) { throw "pip install failed" }

Write-Host "== Chromium to bundle"
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Resolve-Path $Build) "ms-playwright"
& $Py -m playwright install chromium --no-shell
if ($LASTEXITCODE) { throw "playwright install failed" }
Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH

Write-Host "== PyInstaller"
& $Py -m PyInstaller --noconfirm --clean --windowed `
  --name $Name `
  --workpath "$Build\work" --specpath $Build --distpath dist `
  okno.py
if ($LASTEXITCODE) { throw "PyInstaller failed" }

Write-Host "== Bundling Chromium"
Copy-Item -Recurse "$Build\ms-playwright" "dist\$Name\ms-playwright"

Write-Host "== Self-test"
$Report = Join-Path (Resolve-Path $Build) "self-test.txt"
$env:POISK_DATA = Join-Path (Resolve-Path $Build) "data"
# A windowed exe does not block the console, so wait for it explicitly.
$p = Start-Process -FilePath "dist\$Name\$Name.exe" -ArgumentList "--self-test", "`"$Report`"" -Wait -PassThru
Remove-Item Env:POISK_DATA
if (Test-Path $Report) { Get-Content $Report -Encoding UTF8 }
if ($p.ExitCode -ne 0) { throw "Self-test failed (exit code $($p.ExitCode))" }

Write-Host "== Zip"
# Instructions right next to the exe: visible both in the archive and after extracting
Copy-Item packaging\windows-readme.txt "dist\$Name\_ПРОЧТИ_МЕНЯ.txt"
Compress-Archive -Path "dist\$Name" -DestinationPath $Zip -Force
Write-Host "== Done: $Zip ($([math]::Round((Get-Item $Zip).Length / 1MB)) MB)"
