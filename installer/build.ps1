# Builds ChessfishSetup-<version>.exe:  powershell -ExecutionPolicy Bypass -File installer\build.ps1 [-Version 0.1.0]
param([string]$Version = "0.1.0")
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Py)) { py -3 -m venv .venv }
& $Py -m pip install -q -r host\requirements.txt pyinstaller==6.22.3
if ($LASTEXITCODE) { throw "pip install failed" }

# --- Stockfish: download the pinned release and verify its hash ---
$Lock = Get-Content installer\stockfish.lock.json -Raw | ConvertFrom-Json
$Zip = "build\$($Lock.asset)"
New-Item -ItemType Directory -Force build | Out-Null
if (-not (Test-Path $Zip) -or (Get-FileHash $Zip -Algorithm SHA256).Hash -ne $Lock.sha256.ToUpper()) {
    Write-Host "Downloading $($Lock.url)"
    Invoke-WebRequest $Lock.url -OutFile $Zip
}
$Hash = (Get-FileHash $Zip -Algorithm SHA256).Hash
if ($Hash -ne $Lock.sha256.ToUpper()) { throw "Stockfish checksum mismatch: $Hash" }
$Unz = "build\stockfish-unz"
Remove-Item -Recurse -Force $Unz, build\stockfish-dist -ErrorAction SilentlyContinue
Expand-Archive $Zip -DestinationPath $Unz
New-Item -ItemType Directory -Force build\stockfish-dist | Out-Null
Copy-Item (Join-Path $Unz $Lock.exe_in_zip) build\stockfish-dist\stockfish.exe
Copy-Item (Join-Path $Unz $Lock.license_in_zip) build\stockfish-dist\Copying.txt
"Stockfish $($Lock.release) is free software under the GNU GPL v3 (see Copying.txt).`r`nSource code: $($Lock.source)" |
    Set-Content build\stockfish-dist\SOURCE.txt
# bench prints to stderr; cmd /c keeps PowerShell 5.1 from treating that as an error
$bench = cmd /c "build\stockfish-dist\stockfish.exe bench 16 1 8 2>&1" | Select-String "Nodes/second"
if (-not $bench) { throw "Stockfish bench failed" }
Write-Host "Stockfish OK: $bench"

# --- App ---
& $Py -m PyInstaller installer\chessfish.spec --noconfirm --distpath build\dist --workpath build\pyi
if ($LASTEXITCODE) { throw "PyInstaller failed" }
$p = Start-Process build\dist\Chessfish\Chessfish.exe -ArgumentList "--selftest" -Wait -PassThru
if ($p.ExitCode) { throw "frozen selftest failed (see build\dist\Chessfish\logs\chessfish.log)" }
Remove-Item -Recurse -Force build\dist\Chessfish\config, build\dist\Chessfish\logs, build\dist\Chessfish\games -ErrorAction SilentlyContinue

# --- Installer ---
$Iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) { throw "Inno Setup 6 not found (winget install -e --id JRSoftware.InnoSetup)" }
& $Iscc "/DAppVersion=$Version" installer\chessfish.iss
if ($LASTEXITCODE) { throw "ISCC failed" }
Get-Item "build\installer\ChessfishSetup-$Version.exe"
