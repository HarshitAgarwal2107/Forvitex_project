# Starts the voice worker and the dashboard. Ctrl+C stops both.
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { py -3.11 -m venv .venv; & $py -m pip install -q -r requirements.txt }
if (-not (Test-Path ".env")) { Copy-Item .env.example .env; Write-Host "Fill in .env, then run again."; exit 1 }
& $py -c "from app import db; db.init()"
$worker = Start-Process $py -ArgumentList "-m app.worker dev --log-level info" -PassThru -NoNewWindow
try { & $py -m uvicorn app.api:app --port 8000 --no-access-log }
finally { taskkill /PID $worker.Id /T /F | Out-Null }
