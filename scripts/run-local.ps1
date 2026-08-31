param(
    [switch]$Email,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvDirectory = Join-Path $projectRoot ".venv"
$pythonExecutable = Join-Path $venvDirectory "Scripts\python.exe"
$staffFile = Join-Path $projectRoot "staff.json"
$envFile = Join-Path $projectRoot ".env"
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

if ($Email) {
    if (-not (Test-Path -LiteralPath $envFile)) {
        throw ".env was not found. Copy .env.example to .env and add SMTP settings."
    }
    & $pythonExecutable -m lss_report.cli --staff-file $staffFile --env-file $envFile --email
} else {
    & $pythonExecutable -m lss_report.cli --staff-file $staffFile --output $reportFile --excel $workbookFile
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Local report created at $reportFile and $workbookFile"
    }
}

exit $LASTEXITCODE

