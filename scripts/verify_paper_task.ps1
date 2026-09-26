$ErrorActionPreference = 'Stop'
$paperRoot = 'C:\Users\Neila\quant-research'
$paperTaskName = 'QuantResearch-PAPER-20260922'
try {
    $paperTask = Get-ScheduledTask -TaskName $paperTaskName
    $paperInfo = Get-ScheduledTaskInfo -TaskName $paperTaskName
    $paperUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $paperAction = @($paperTask.Actions)
    $paperTrigger = @($paperTask.Triggers)
    $paperExpectedArguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Users\Neila\quant-research\scripts\start_paper_bot.ps1"'
    $paperChecks = [ordered]@{
        enabled = ($paperTask.Settings.Enabled -and $paperTask.State -ne 'Disabled')
        exact_action = ($paperAction.Count -eq 1 -and $paperAction[0].Execute -eq "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -and $paperAction[0].Arguments -ceq $paperExpectedArguments)
        working_directory = ($paperAction[0].WorkingDirectory -eq $paperRoot)
        one_time_trigger = ($paperTrigger.Count -eq 1 -and $paperTrigger[0].CimClass.CimClassName -eq 'MSFT_TaskTimeTrigger' -and !$paperTrigger[0].Repetition.Interval)
        correct_start = ([DateTimeOffset]::Parse($paperTrigger[0].StartBoundary) -eq [DateTimeOffset]::Parse('2026-09-22T12:45:00Z'))
        expires = ([DateTimeOffset]::Parse($paperTrigger[0].EndBoundary) -eq [DateTimeOffset]::Parse('2026-09-22T14:00:00Z'))
        interactive_user = ($paperTask.Principal.LogonType -eq 'Interactive' -and $paperTask.Principal.UserId -in @($paperUser.Name, $paperUser.User.Value, $env:USERNAME))
        limited_privilege = ($paperTask.Principal.RunLevel -eq 'Limited')
        start_when_available = [bool]$paperTask.Settings.StartWhenAvailable
        no_parallel_instances = ($paperTask.Settings.MultipleInstances -eq 'IgnoreNew')
        wake_to_run = [bool]$paperTask.Settings.WakeToRun
        bounded_lifetime = ($paperTask.Settings.ExecutionTimeLimit -eq 'PT2H')
        automatic_expiry_cleanup = ($paperTask.Settings.DeleteExpiredTaskAfter -eq 'PT1H')
    }
    $paperPassed = !($paperChecks.Values -contains $false)
    $paperResult = [ordered]@{ at = (Get-Date).ToUniversalTime().ToString('o'); task_name = $paperTaskName; passed = $paperPassed
        checks = $paperChecks; next_run = $paperInfo.NextRunTime.ToString('o'); state = [string]$paperTask.State
        startup_utc = '2026-09-22T12:45:00Z'; startup_vancouver = '2026-09-22T05:45:00-07:00' }
    $paperJson = $paperResult | ConvertTo-Json -Depth 5
    $paperOutput = Join-Path $paperRoot 'state\paper_checks\windows-task.json'
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($paperOutput)) | Out-Null
    [IO.File]::WriteAllText($paperOutput, $paperJson, [Text.UTF8Encoding]::new($false))
    Write-Output $paperJson
    if (!$paperPassed) { exit 2 }
} catch {
    [Console]::Error.WriteLine('PAPER task verification failed: ' + $_.Exception.Message)
    exit 2
}
