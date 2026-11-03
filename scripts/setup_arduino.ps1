$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath '.venv-arduino\Scripts\python.exe')) {
        python -m venv .venv-arduino
        if ($LASTEXITCODE -ne 0) { throw 'Failed to create Arduino tool environment.' }
    }
    & '.\.venv-arduino\Scripts\python.exe' -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { throw 'Failed to initialize pip.' }
    & '.\.venv-arduino\Scripts\python.exe' -m pip install -r requirements-arduino.txt
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install Arduino tools.' }
} finally {
    Pop-Location
}
