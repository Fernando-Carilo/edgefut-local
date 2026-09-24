<#
.SYNOPSIS
  Ambiente de desenvolvimento do EdgeFut AI (Windows).
  Sobe o engine Python (127.0.0.1:8765) e a janela Tauri com hot reload do frontend.

.NOTES
  Requisitos: Python 3.12, uv (opcional), Node 20+, pnpm, Rust (rustup) e o
  WebView2 (já presente no Windows 10/11). Nunca expõe portas fora do loopback.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Ensure-Venv {
  if (-not (Test-Path "engine\.venv\Scripts\python.exe")) {
    Write-Host "[engine] criando venv..." -ForegroundColor Cyan
    if (Get-Command uv -ErrorAction SilentlyContinue) {
      Push-Location engine; uv venv .venv --python 3.12; uv pip install -e ".[dev]" --python .venv\Scripts\python.exe; Pop-Location
    } else {
      python -m venv engine\.venv
      & engine\.venv\Scripts\python.exe -m pip install --upgrade pip
      & engine\.venv\Scripts\python.exe -m pip install -e "engine[dev]"
    }
  }
}

Ensure-Venv
if (-not (Test-Path "node_modules")) { pnpm install }

# O build script do Tauri exige que o arquivo do sidecar exista. Em dev o engine é iniciado
# por este script (abaixo) e o shell Rust detecta a porta ocupada e NÃO inicia o sidecar,
# então um placeholder basta. build-windows.ps1 substitui pelo binário PyInstaller real.
$bin = "apps\desktop\src-tauri\binaries"
New-Item -ItemType Directory -Force -Path $bin | Out-Null
$sidecar = "$bin\edgefut-engine-x86_64-pc-windows-msvc.exe"
if (-not (Test-Path $sidecar)) { Set-Content -Path $sidecar -Value "placeholder-dev" -Encoding ASCII }

Write-Host "[engine] iniciando em http://127.0.0.1:8765 ..." -ForegroundColor Cyan
$engine = Start-Process -PassThru -NoNewWindow -WorkingDirectory "$Root\engine" `
  -FilePath "$Root\engine\.venv\Scripts\python.exe" -ArgumentList "-m", "edgefut.main", "--reload"

try {
  Write-Host "[desktop] iniciando Tauri (Vite em http://127.0.0.1:1420) ..." -ForegroundColor Cyan
  pnpm --filter @edgefut/desktop tauri dev
} finally {
  if ($engine -and -not $engine.HasExited) { Stop-Process -Id $engine.Id -Force }
}
