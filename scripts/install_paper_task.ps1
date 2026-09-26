$ErrorActionPreference = 'Stop'
$paperRoot = 'C:\Users\Neila\quant-research'
Set-Location -LiteralPath $paperRoot
$paperTaskName = 'QuantResearch-PAPER-20260922'
$paperAt = [DateTimeOffset]::Parse('2026-09-22T05:45:00-07:00')
if ([DateTimeOffset]::Now -ge $paperAt) { throw 'The one-time startup time has passed; do not install a catch-up task.' }
$paperUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$paperAction = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -Argument '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Users\Neila\quant-research\scripts\start_paper_bot.ps1"' `
    -WorkingDirectory $paperRoot
$paperTrigger = New-ScheduledTaskTrigger -Once -At $paperAt.LocalDateTime
$paperTrigger.StartBoundary = '2026-09-22T05:45:00-07:00'
$paperTrigger.EndBoundary = '2026-09-22T07:00:00-07:00'
$paperPrincipal = New-ScheduledTaskPrincipal -UserId $paperUser -LogonType Interactive -RunLevel Limited
$paperSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -WakeToRun `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -DeleteExpiredTaskAfter (New-TimeSpan -Hours 1)
$paperTask = New-ScheduledTask -Action $paperAction -Trigger $paperTrigger -Principal $paperPrincipal -Settings $paperSettings `
    -Description 'One-time, one-batch PAPER opening watcher. Requires authenticated TWS PAPER, passing attestation and explicit arm. No LIVE or forecast generation.'
if (Get-ScheduledTask -TaskName $paperTaskName -ErrorAction SilentlyContinue) {
    throw 'Task already exists. Use verify_paper_task.ps1; this installer will not overwrite it.'
}
Register-ScheduledTask -TaskName $paperTaskName -InputObject $paperTask | Out-Null
& (Join-Path $paperRoot 'scripts\verify_paper_task.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
