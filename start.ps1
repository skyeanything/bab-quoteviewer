$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path $python)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'
        if ($LASTEXITCODE -ne 0) { Write-Error 'Python 3.11 or later is required'; exit 1 }
        & py -3 -m venv .venv
    } else {
        & python -c 'import sys; sys.exit(sys.version_info < (3, 11))'
        if ($LASTEXITCODE -ne 0) { Write-Error 'Python 3.11 or later is required'; exit 1 }
        & python -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python start.py @args
exit $LASTEXITCODE
