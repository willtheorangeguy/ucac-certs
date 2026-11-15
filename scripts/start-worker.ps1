param([switch]$Install, [switch]$EnableStartup)
$ErrorActionPreference = 'Stop'
$workerProject = Split-Path -Parent $PSScriptRoot
$workerVenv = Join-Path $workerProject '.venv-worker'
$workerPython = Join-Path $workerVenv 'Scripts/python.exe'
$workerPythonWindowless = Join-Path $workerVenv 'Scripts/pythonw.exe'
$workerConfig = Join-Path $workerProject '.env.worker'
$workerLog = Join-Path $workerProject 'data/local-worker.log'
if (-not (Test-Path -LiteralPath $workerConfig)) {
    throw 'Missing .env.worker. Configure WORKER_BASE_URL and LOCAL_WORKER_TOKEN first.'
}
if ($Install -or -not (Test-Path -LiteralPath $workerPython)) {
    if (-not (Test-Path -LiteralPath $workerPython)) { python -m venv $workerVenv }
    & $workerPython -m pip install -e $workerProject
    if ($LASTEXITCODE -ne 0) { throw 'Local scanner installation failed.' }
    & $workerPython -m playwright install firefox
    if ($LASTEXITCODE -ne 0) { throw 'Firefox installation failed.' }
}
$workerArguments = "-m lss_report.worker --env-file `"$workerConfig`" --log-file `"$workerLog`""
if ($EnableStartup) {
    $workerStartup = [Environment]::GetFolderPath('Startup')
    $workerShortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $workerStartup 'UCAC Local Scanner.lnk'))
    $workerShortcut.TargetPath = $workerPythonWindowless
    $workerShortcut.Arguments = $workerArguments
    $workerShortcut.WorkingDirectory = $workerProject
    $workerShortcut.WindowStyle = 7
    $workerShortcut.Description = 'Run authorized certification lookups for the UCAC dashboard.'
    $workerShortcut.Save()
}
$workerProcess = Start-Process -FilePath $workerPythonWindowless -ArgumentList $workerArguments -WorkingDirectory $workerProject -WindowStyle Hidden -PassThru
Write-Host "Local scanner started (process $($workerProcess.Id)). Status log: $workerLog"
