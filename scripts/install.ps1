<#
.SYNOPSIS
  Instala as dependências de desenvolvimento do EdgeFut AI no Windows (idempotente).
  Não instala nada relacionado a apostas; apenas toolchain: Python 3.12, uv, Node LTS, pnpm, Rust, NSIS (opcional).
#>
$ErrorActionPreference = "Stop"

function Has($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function WingetInstall($id) {
  if (Has winget) { winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements | Out-Null }
  else { Write-Warning "winget não encontrado; instale manualmente: $id" }
}

if (-not (Has python)) { Write-Host "Instalando Python 3.12..."; WingetInstall "Python.Python.3.12" }
if (-not (Has uv))     { Write-Host "Instalando uv...";         WingetInstall "astral-sh.uv" }
if (-not (Has node))   { Write-Host "Instalando Node LTS...";   WingetInstall "OpenJS.NodeJS.LTS" }
if (-not (Has pnpm))   { Write-Host "Instalando pnpm...";       npm install -g pnpm }
if (-not (Has cargo))  { Write-Host "Instalando Rust...";       WingetInstall "Rustlang.Rustup" }
if (-not (Has makensis)) { Write-Host "NSIS (para o instalador) é baixado automaticamente pelo Tauri no build." }

# Build Tools do Visual Studio (linker MSVC) são necessários para o Rust no Windows.
if (-not (Test-Path "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe")) {
  Write-Host "Instalando Visual Studio Build Tools (C++)..."
  WingetInstall "Microsoft.VisualStudio.2022.BuildTools"
  Write-Warning "Abra o Visual Studio Installer e marque 'Desenvolvimento para desktop com C++' se o build do Tauri falhar."
}

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Push-Location engine
if (-not (Test-Path ".venv")) { uv venv .venv --python 3.12 }
uv pip install -e ".[dev]" --python .venv\Scripts\python.exe
Pop-Location
pnpm install

Write-Host ""
Write-Host "Pronto. Próximos passos:" -ForegroundColor Green
Write-Host "  .\scripts\dev.ps1            # desenvolvimento"
Write-Host "  .\scripts\build-windows.ps1  # gera EdgeFutAI-Setup.exe"
