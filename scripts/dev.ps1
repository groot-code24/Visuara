# Local development on Windows without Docker: SQLite + local disk + email outbox.
#   .\scripts\dev.ps1 setup   create .venv, install Python and web dependencies (Python 3.11+ via `py`, Node 20+, FFmpeg on PATH)
#   .\scripts\dev.ps1 all     everything: API, worker and web app; then open http://localhost:3000
#   .\scripts\dev.ps1 run     API on http://127.0.0.1:8000 plus one worker in a second window
#   .\scripts\dev.ps1 web     web app on http://localhost:3000 (run in a second terminal); open this one
#   .\scripts\dev.ps1 test    run the backend test suite
# PostgreSQL and a real SMTP inbox locally: docker compose up --build
param([ValidateSet('setup', 'all', 'run', 'web', 'test')][string]$Command = 'all')
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$py = if (Test-Path '.venv311\Scripts\python.exe') { '.venv311\Scripts\python.exe' } else { '.venv\Scripts\python.exe' }
if (($Command -in 'all', 'run') -and -not (Test-Path '.env')) { Copy-Item '.env.example' '.env' }
if (-not $env:CC_MAIL_BACKEND) { $env:CC_MAIL_BACKEND = 'outbox' }
$env:CC_INLINE_WORKER = '0'

switch ($Command) {
  'setup' {
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { throw 'FFmpeg is required on PATH (winget install Gyan.FFmpeg)' }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'Node.js 20+ is required (winget install OpenJS.NodeJS.LTS)' }
    py -3.11 -m venv .venv; if (-not $?) { py -3.12 -m venv .venv }
    & $py -m pip install -q --upgrade pip
    & $py -m pip install -q -r requirements-dev.txt
    if (-not (Test-Path .env)) { Copy-Item .env.example .env }
    & $py -m ops.migrate
    Push-Location web; npm ci --no-audit --no-fund; Pop-Location
    Write-Output 'setup complete: .\scripts\dev.ps1 run   (and .\scripts\dev.ps1 web in a second terminal)'
  }
  'all' {
    $listening = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
    if (-not $listening) { Start-Process powershell -ArgumentList '-NoExit', '-File', (Join-Path $PSScriptRoot 'dev.ps1'), 'web' }
    $worker = Start-Process -FilePath $py -ArgumentList 'worker.py' -PassThru
    Write-Output 'Open http://localhost:3000'
    try { & $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --timeout-keep-alive 75 --reload }
    finally { Stop-Process -Id $worker.Id -ErrorAction SilentlyContinue }
  }
  'run' {
    $worker = Start-Process -FilePath $py -ArgumentList 'worker.py' -PassThru
    try { & $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --timeout-keep-alive 75 --reload }
    finally { Stop-Process -Id $worker.Id -ErrorAction SilentlyContinue }
  }
  'web' {
    if (-not $env:CC_API_ORIGIN) { $env:CC_API_ORIGIN = 'http://127.0.0.1:8000' }
    Push-Location web; try { npm run dev } finally { Pop-Location }
  }
  'test' {
    $env:PYTHONPATH = '.;tests'
    & $py -m pytest -q -p no:cacheprovider
  }
}
