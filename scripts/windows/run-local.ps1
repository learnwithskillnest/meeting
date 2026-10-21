# HeartSync - one-click local run for Windows (development mode).
# Finds Python 3.10+, creates a private virtual environment, installs packages,
# loads fictional demo members on first run, then starts the site.
# Nothing is sent anywhere: emails/SMS go to http://localhost:8000/dev/outbox.
$ErrorActionPreference = 'Continue'
$app = Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '..\..')
Set-Location $app
$log = Join-Path $app 'run-local-log.txt'
Start-Transcript -Path $log -Force | Out-Null
$demoPassword = 'Demo-pass-2026!'
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'   # Windows console safety

function Step($m) { Write-Host ""; Write-Host "=== $m ===" -ForegroundColor Green }
function Fail($m) {
  Write-Host ""; Write-Host "STOPPED: $m" -ForegroundColor Red
  Write-Host "A log was saved to $log"
  Stop-Transcript | Out-Null
  exit 1
}

# 1. Python 3.10+
Step 'Finding Python'
$candidates = @(@{exe='py'; pre=@('-3')}, @{exe='python'; pre=@()}, @{exe='python3'; pre=@()})
foreach ($p in @("$env:USERPROFILE\anaconda3\python.exe", "$env:USERPROFILE\miniconda3\python.exe",
                 "$env:LOCALAPPDATA\anaconda3\python.exe", "C:\ProgramData\anaconda3\python.exe",
                 "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                 "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe")) {
  if (Test-Path $p) { $candidates += @{exe=$p; pre=@()} }
}
$py = $null
foreach ($c in $candidates) {
  if (-not (Get-Command $c.exe -ErrorAction SilentlyContinue)) { continue }
  $pre = $c.pre
  $ver = (& $c.exe @pre -c "import sys;print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null | Select-Object -Last 1)
  if ($LASTEXITCODE -eq 0 -and "$ver" -match '^3\.(\d+)$' -and [int]$Matches[1] -ge 10) { $py = $c; break }
}
if (-not $py) {
  Write-Host 'Python 3.10 or newer not found. Installing Python 3.12 (a few minutes)...'
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent
  $p312 = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
  if (Test-Path $p312) { $py = @{exe=$p312; pre=@()} } else { Fail 'Could not install Python. Install Python 3.12 from https://www.python.org and run this again.' }
}
$pre = $py.pre
Write-Host "Using: $($py.exe) $pre ($(& $py.exe @pre --version 2>&1))"

# 2. Virtual environment + packages
Step 'Installing packages (first run only takes a minute or two)'
$venvPy = Join-Path $app '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
  & $py.exe @pre -m venv .venv
  if (-not (Test-Path $venvPy)) { Fail 'Could not create the virtual environment (.venv).' }
}
& $venvPy -m pip install --disable-pip-version-check -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail 'Package install failed. Check your internet connection and run this again.' }

# 3. Settings and demo data
Step 'Preparing demo data'
if (-not (Test-Path (Join-Path $app '.env'))) { Copy-Item (Join-Path $app '.env.example') (Join-Path $app '.env') }
$env:APP_ENV = 'development'
$env:SEED_PASSWORD = $demoPassword
if (-not (Test-Path (Join-Path $app 'var\heartsync.db'))) {
  & $venvPy scripts\seed.py --count 30
  if ($LASTEXITCODE -ne 0) { Fail 'Loading demo data failed.' }
  $env:ADMIN_EMAIL = 'admin@heartsync.test'; $env:ADMIN_PASSWORD = $demoPassword
  & $venvPy scripts\create_admin.py
  Remove-Item Env:ADMIN_PASSWORD
} else { Write-Host 'Demo data already loaded (delete the var folder to start fresh).' }

# 4. Port
function PortInUse($p) {
  try { $c = New-Object System.Net.Sockets.TcpClient; $r = $c.BeginConnect('127.0.0.1', $p, $null, $null)
        $ok = $r.AsyncWaitHandle.WaitOne(300) -and $c.Connected; $c.Close(); return $ok } catch { return $false }
}
$port = 8000
while ((PortInUse $port) -and $port -lt 8010) { $port++ }
$url = "http://localhost:$port"

# 5. Start
Step 'Starting HeartSync'
Write-Host "Open $url" -ForegroundColor Cyan
Write-Host "Logins (password $demoPassword):"
Write-Host "  demo@heartsync.test   and  demo2@heartsync.test  (already matched - try the chat in two browsers)"
Write-Host "  admin@heartsync.test  (moderation dashboard at $url/admin)"
Write-Host "Verification codes and emails: $url/dev/outbox"
Write-Host "Keep this window open while you use the site. Close it (or press Ctrl+C) to stop."
Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile -Command Start-Sleep 4; Start-Process '$url'"
$env:BASE_URL = $url
& $venvPy -m uvicorn heartsync.main:app --host 127.0.0.1 --port $port --log-level warning
Stop-Transcript | Out-Null
