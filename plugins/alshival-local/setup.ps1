param([string]$AppDirectory, [switch]$ReadOnly, [string]$Output)
$ErrorActionPreference = 'Stop'
if (-not $AppDirectory) {
    $AppDirectory = Join-Path $env:LOCALAPPDATA 'Programs\Alshival.Ai'
}
$python = Join-Path $AppDirectory 'resources\python\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Install Alshival first, or pass -AppDirectory with your custom installation folder.'
}
$arguments = @((Join-Path $PSScriptRoot 'configure.py'), '--app-directory', $AppDirectory)
if ($ReadOnly) { $arguments += '--read-only' }
if ($Output) { $arguments += @('--output', $Output) }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Alshival Local setup failed.' }
