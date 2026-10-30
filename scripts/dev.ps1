# Local development on Windows without Docker: SQLite + local disk + email outbox, API and a separate worker.
#   .\scripts\dev.ps1 setup   create .venv and install dependencies (Python 3.11+ via `py`, FFmpeg on PATH)
#   .\scripts\dev.ps1 run     API on http://127.0.0.1:8000 plus one worker in a second window
#   .\scripts\dev.ps1 test    run the test suite
# Production-shaped stack (PostgreSQL, S3 stand-in, SMTP inbox, two workers): docker compose up --build
param([ValidateSet('setup', 'run', 'test')][string]$Command = 'run')
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$py = Join-Path '.venv' 'Scripts\python.exe'
if (-not $env:CC_MAIL_BACKEND) { $env:CC_MAIL_BACKEND = 'outbox' }
$env:CC_INLINE_WORKER = '0'

switch ($Command) {
  'setup' {
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { throw 'FFmpeg is required on PATH (winget install Gyan.FFmpeg)' }
    py -3.11 -m venv .venv; if (-not $?) { py -3.12 -m venv .venv }
    & $py -m pip install -q --upgrade pip
    & $py -m pip install -q -r requirements-dev.txt
    if (-not (Test-Path .env)) { Copy-Item .env.example .env }
    & $py -m ops.migrate
    Write-Output 'setup complete: .\scripts\dev.ps1 run'
  }
  'run' {
    $worker = Start-Process -FilePath $py -ArgumentList 'worker.py' -PassThru
    try { & $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload }
    finally { Stop-Process -Id $worker.Id -ErrorAction SilentlyContinue }
  }
  'test' {
    $env:PYTHONPATH = '.;tests'
    & $py -m pytest -q -p no:cacheprovider
  }
}
