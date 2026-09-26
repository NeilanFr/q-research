param([switch]$MonitorOnly, [int]$MaxSeconds = 0)
$ErrorActionPreference = 'Stop'
$paperRoot = 'C:\Users\Neila\quant-research'
$paperExit = 2
$paperLock = $null
$paperLog = $null
try {
    Set-Location -LiteralPath $paperRoot
    $paperLogDir = Join-Path $paperRoot 'state\private\logs'
    New-Item -ItemType Directory -Path $paperLogDir -Force | Out-Null
    $paperStamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffffffZ') + '-' + $PID
    $paperLog = Join-Path $paperLogDir ($paperStamp + '-startup.log')
    $paperStdout = Join-Path $paperLogDir ($paperStamp + '-stdout.log')
    $paperStderr = Join-Path $paperLogDir ($paperStamp + '-stderr.log')
    Add-Content -LiteralPath $paperLog -Value ('Starting PAPER watcher at ' + (Get-Date).ToUniversalTime().ToString('o'))
    $paperLock = [IO.File]::Open((Join-Path $paperRoot 'state\private\paper-launcher.lock'),
        [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    $paperPython = Join-Path $paperRoot '.venv\Scripts\python.exe'
    if (!(Test-Path -LiteralPath $paperPython)) { throw 'Repository virtualenv Python is missing.' }
    if ($MaxSeconds -ne 0 -and (!$MonitorOnly -or $MaxSeconds -lt 1 -or $MaxSeconds -gt 300)) {
        throw 'MaxSeconds requires MonitorOnly and must be 1..300.'
    }
    $paperArguments = @('-u', '-m', 'quantlab.paper_cli', 'watch', '--batch', 'model_3de7b630030340cd')
    if ($MonitorOnly) { $paperArguments += '--monitor-only' }
    if ($MaxSeconds -gt 0) { $paperArguments += @('--max-seconds', [string]$MaxSeconds, '--poll-seconds', '5') }
    Add-Content -LiteralPath $paperLog -Value ('stdout=' + $paperStdout + [Environment]::NewLine + 'stderr=' + $paperStderr)
    # The scheduled PowerShell host is already hidden. Direct invocation also
    # avoids Start-Process failing when inherited PATH/Path keys coexist.
    $paperPreviousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $paperPython @paperArguments 1> $paperStdout 2> $paperStderr
        $paperExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $paperPreviousErrorAction }
    Add-Content -LiteralPath $paperLog -Value ('Watcher exited with code ' + $paperExit)
    if ($paperExit -ne 0) { throw ('Watcher failed with exit code ' + $paperExit + '; see ' + $paperStderr) }
} catch {
    $paperExit = 2
    $paperMessage = 'PAPER STARTUP FAILED: ' + $_.Exception.Message
    if ($paperLog) { Add-Content -LiteralPath $paperLog -Value $paperMessage }
    [Console]::Error.WriteLine($paperMessage)
} finally {
    if ($paperLock) { $paperLock.Dispose() }
}
exit $paperExit
