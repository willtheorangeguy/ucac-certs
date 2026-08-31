param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvDirectory = Join-Path $projectRoot ".venv"
$pythonExecutable = Join-Path $venvDirectory "Scripts\python.exe"
$staffFile = Join-Path $projectRoot "staff.json"
$reportFile = Join-Path $projectRoot "report.pdf"
$workbookFile = Join-Path $projectRoot "report.xlsx"

if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    python -m venv $venvDirectory
}

if (-not $SkipInstall) {
    & $pythonExecutable -m pip install -e "$projectRoot[test]"
}

if (-not (Test-Path -LiteralPath $staffFile)) {
    throw "staff.json was not found. Copy staff.example.json to staff.json and add authorized records."
}

& $pythonExecutable -m lss_report.cli --staff-file $staffFile --output $reportFile --excel $workbookFile
if ($LASTEXITCODE -eq 0) {
    Write-Host "Local report created at $reportFile and $workbookFile"
}

exit $LASTEXITCODE
