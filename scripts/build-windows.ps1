<#
.SYNOPSIS
  Gera o instalador Windows `EdgeFutAI-Setup.exe`.
  1) PyInstaller empacota o engine Python como sidecar único (edgefut-engine.exe)
  2) Vite compila o frontend
  3) Tauri gera o NSIS installer

.PARAMETER SkipEngine
  Reutiliza o sidecar já gerado em apps/desktop/src-tauri/binaries.
#>
param([switch]$SkipEngine)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Py = "$Root\engine\.venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { throw "venv não encontrada. Rode scripts\install.ps1 primeiro." }

$Triple = (rustc -vV | Select-String "host:").ToString().Split(":")[1].Trim()   # x86_64-pc-windows-msvc
$BinDir = "$Root\apps\desktop\src-tauri\binaries"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

if (-not $SkipEngine) {
  Write-Host "[1/3] Empacotando engine com PyInstaller..." -ForegroundColor Cyan
  Push-Location "$Root\engine"
  & $Py -m pytest -q
  & $Py -m PyInstaller edgefut-engine.spec --noconfirm --clean --distpath dist --workpath build
  Pop-Location
  Copy-Item "$Root\engine\dist\edgefut-engine.exe" "$BinDir\edgefut-engine-$Triple.exe" -Force

  Write-Host "      validando sidecar (/health)..." -ForegroundColor DarkGray
  $p = Start-Process -PassThru -NoNewWindow "$BinDir\edgefut-engine-$Triple.exe" -ArgumentList "--port","8799","--no-bootstrap","--no-scheduler"
  try {
    $ok = $false
    for ($i = 0; $i -lt 60 -and -not $ok; $i++) {
      Start-Sleep -Milliseconds 500
      try { $ok = (Invoke-RestMethod "http://127.0.0.1:8799/health" -TimeoutSec 2).status -eq "ok" } catch {}
    }
    if (-not $ok) { throw "sidecar não respondeu em /health" }
  } finally { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
}

$sidecar = Get-Item "$BinDir\edgefut-engine-$Triple.exe" -ErrorAction Stop
if ($sidecar.Length -lt 1MB) { throw "Sidecar em $($sidecar.FullName) parece ser um placeholder de dev. Rode sem -SkipEngine." }

Write-Host "[2/3] Frontend (typecheck + Vite build)..." -ForegroundColor Cyan
pnpm install --frozen-lockfile
pnpm --filter @edgefut/contracts typecheck
pnpm --filter @edgefut/desktop build

Write-Host "[3/3] Tauri bundle (NSIS)..." -ForegroundColor Cyan
pnpm --filter @edgefut/desktop tauri build --bundles nsis

$setup = Get-ChildItem "$Root\apps\desktop\src-tauri\target\release\bundle\nsis\*.exe" | Select-Object -First 1
$out = "$Root\dist"
New-Item -ItemType Directory -Force -Path $out | Out-Null
Copy-Item $setup.FullName "$out\EdgeFutAI-Setup.exe" -Force
Write-Host ""
Write-Host "Instalador gerado: $out\EdgeFutAI-Setup.exe" -ForegroundColor Green
