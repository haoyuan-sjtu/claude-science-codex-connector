#Requires -Version 5.1
<#
.SYNOPSIS
  start-windows.ps1 — One-click startup for Claude Science + Codex bridge (Windows)

.DESCRIPTION
  1. Starts the local bridge proxy (port 9876) in the background
  2. Waits until the proxy is healthy
  3. Quits any running Claude Science, then relaunches it with
     ANTHROPIC_BASE_URL pointed at the bridge

.PARAMETER ProxyOnly
  Start only the proxy, do not launch Claude Science.

.PARAMETER Stop
  Stop the proxy and Claude Science, then exit.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\start-windows.ps1
  powershell -ExecutionPolicy Bypass -File .\start-windows.ps1 -ProxyOnly
  powershell -ExecutionPolicy Bypass -File .\start-windows.ps1 -Stop

.NOTES
  Windows support is untested (see README). If Claude Science on Windows
  does not honor ANTHROPIC_BASE_URL, fall back to launching it from a
  terminal where you have run `setx ANTHROPIC_BASE_URL http://127.0.0.1:9876`
  (then open a NEW terminal so the var is visible).
#>

[CmdletBinding()]
param(
  [switch]$ProxyOnly,
  [switch]$Stop
)

$ErrorActionPreference = "Stop"

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$BridgeDir   = $ScriptDir
$DataDir     = Join-Path $env:USERPROFILE ".claude-science"
$LogDir      = Join-Path $DataDir "logs"
$ProxyLog    = Join-Path $LogDir "bridge-proxy.log"
$PidFile     = Join-Path $LogDir "bridge-proxy.pid"

$ProxyHost = "127.0.0.1"
$ProxyPort = 9876
$BaseUrl   = "http://${ProxyHost}:${ProxyPort}"
$VenvPy    = Join-Path $BridgeDir ".venv\Scripts\python.exe"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Ok   { param($m) Write-Host "[ok]   $m" -ForegroundColor Green }
function Write-Info { param($m) Write-Host "[start] $m" -ForegroundColor Cyan }
function Write-Warn2{ param($m) Write-Host "[warn] $m" -ForegroundColor Yellow }
function Write-Err2 { param($m) Write-Host "[error] $m" -ForegroundColor Red }

# Ensure a dedicated virtualenv with the proxy's dependencies exists.
# Falls back to auto-creating it from any available python (one-time).
function Ensure-Venv {
  if (Test-Path $VenvPy) { return }
  Write-Info "Creating virtualenv (.venv) — one-time setup ..."
  $base = (Get-Command python -ErrorAction SilentlyContinue)
  if (-not $base) { $base = (Get-Command python3 -ErrorAction SilentlyContinue) }
  if (-not $base) { Write-Err2 "python not found on PATH. Install Python 3.9+ first."; exit 1 }
  & $base.Source -m venv (Join-Path $BridgeDir ".venv")
  if ($LASTEXITCODE -ne 0) { Write-Err2 "venv creation failed."; exit 1 }
  Write-Info "Installing requirements.txt into .venv ..."
  $pipExe = Join-Path $BridgeDir ".venv\Scripts\pip.exe"
  & $pipExe install --quiet --upgrade pip
  & $pipExe install --quiet -r (Join-Path $BridgeDir "requirements.txt")
  if ($LASTEXITCODE -ne 0) { Write-Err2 "pip install -r requirements.txt failed."; exit 1 }
  Write-Ok "Virtualenv ready"
}

function Test-PortListening {
  param([int]$Port)
  $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  return [bool]$c
}

function Test-ProxyHealthy {
  try {
    $r = Invoke-WebRequest -Uri "$BaseUrl/health" -TimeoutSec 2 -UseBasicParsing
    return ($r.StatusCode -eq 200)
  } catch {
    return $false
  }
}

if ($Stop) {
  Write-Info "Stopping Claude Science..."
  foreach ($n in @("Claude Science","ClaudeScience","claude-science")) {
    Get-Process -Name $n -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  }
  Write-Info "Stopping bridge proxy..."
  if (Test-Path $PidFile) {
    try { Stop-Process -Id (Get-Content $PidFile) -Force -ErrorAction SilentlyContinue } catch {}
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
  }
  Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*proxy.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 1
  Write-Ok "Stopped."
  exit 0
}

# --- 1. Start bridge proxy -------------------------------------------------
if (Test-PortListening -Port $ProxyPort) {
  Write-Ok "Bridge proxy already running on :$ProxyPort"
} else {
  Write-Info "Launching bridge proxy..."
  Set-Location $BridgeDir

  # Ensure a local config.json exists (start.sh does the same)
  if (-not (Test-Path (Join-Path $BridgeDir "config.json")) -and
          (Test-Path (Join-Path $BridgeDir "config.example.json"))) {
    Copy-Item (Join-Path $BridgeDir "config.example.json") (Join-Path $BridgeDir "config.json")
  }

  # Use the dedicated venv python (auto-created on first run)
  Ensure-Venv

  # Refresh the BYOK OAuth token if the encryption key is present
  if (Test-Path (Join-Path $DataDir "encryption.key")) {
    try { & $VenvPy (Join-Path $BridgeDir "setup-token.py") *>$null } catch {}
  }

  $p = Start-Process -FilePath $VenvPy `
        -ArgumentList "`"$BridgeDir\proxy.py`"" `
        -WindowStyle Hidden `
        -PassThru `
        -RedirectStandardOutput $ProxyLog `
        -RedirectStandardError  (Join-Path $LogDir "bridge-proxy.err.log")
  $p.Id | Out-File -FilePath $PidFile -Encoding ascii

  $healthy = $false
  for ($i = 0; $i -lt 30; $i++) {
    if (Test-ProxyHealthy) { $healthy = $true; break }
    Start-Sleep -Milliseconds 500
  }
  if (-not $healthy) {
    Write-Err2 "Bridge proxy did not become healthy. Check: $ProxyLog"
    exit 1
  }
  Write-Ok "Bridge proxy is healthy"
}

Write-Host "        Dashboard: $BaseUrl/dashboard"
Write-Host "        Health:    $BaseUrl/health"
Write-Host "        Proxy log: $ProxyLog"

if ($ProxyOnly) {
  Write-Ok "Proxy-only mode. Claude Science not launched."
  exit 0
}

# --- 2. Quit existing Claude Science --------------------------------------
Write-Info "Quitting any running Claude Science..."
foreach ($n in @("Claude Science","ClaudeScience","claude-science")) {
  Get-Process -Name $n -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

# --- 3. Locate Claude Science executable ----------------------------------
# Search common install locations; extend this list if yours differs.
$candidates = @(
  "$env:LOCALAPPDATA\Programs\claude-science\Claude Science.exe",
  "$env:LOCALAPPDATA\Programs\Claude Science\Claude Science.exe",
  "$env:LOCALAPPDATA\claude-science\Claude Science.exe",
  "$env:ProgramFiles\Claude Science\Claude Science.exe",
  "${env:ProgramFiles(x86)}\Claude Science\Claude Science.exe"
)
$exe = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (-not $exe) {
  Write-Err2 "Claude Science executable not found in common locations."
  Write-Warn2 "Searched:"
  $candidates | ForEach-Object { Write-Host "          - $_" }
  Write-Warn2 "Edit this script's `$candidates list to point at your install, then rerun."
  Write-Warn2 "The proxy is running — you can also launch Claude Science yourself from a"
  Write-Warn2 "terminal that has ANTHROPIC_BASE_URL=$BaseUrl set."
  exit 1
}

# --- 4. Launch Claude Science with env var --------------------------------
$env:ANTHROPIC_BASE_URL = $BaseUrl
Write-Info "Launching Claude Science with ANTHROPIC_BASE_URL=$BaseUrl ..."
Write-Info "  exe: $exe"
Start-Process -FilePath $exe

Write-Host ""
Write-Ok "Done. Claude Science is launching through the bridge."
Write-Host "        To stop: powershell -ExecutionPolicy Bypass -File .\start-windows.ps1 -Stop"
