param([int]$Port = 43120)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $project
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Create the virtual environment and install server requirements first; see docs/server.md.' }
$ready = & $python -c "import sys; sys.path.insert(0,'service'); from alshitasks.paths import Paths; from alshitasks.server_auth import Auth; print(int(Auth(Paths('server-data')).ready()))"
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect server setup.' }
if ($ready -ne '1') {
    Write-Host 'Choose your owner password below. Input is hidden; press Enter after each entry.'
    & $python service/alshitasks-cli.py server-setup --data-dir server-data --key-file server-keys/master.key
    if ($LASTEXITCODE -ne 0) { throw 'Setup did not complete. Run this launcher again to retry.' }
}
Write-Host "Open http://127.0.0.1:$Port in your browser. Keep this window open while using the server."
& $python service/alshitasks-cli.py server --data-dir server-data --key-file server-keys/master.key --port $Port --base-url "http://127.0.0.1:$Port"
