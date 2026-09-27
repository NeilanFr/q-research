$paperRoot = Split-Path -Parent $PSScriptRoot
function Get-PaperSchedule {
    $paperScheduleJson = & (Join-Path $paperRoot '.venv\Scripts\python.exe') -m quantlab.paper_cli scheduled-opening
    if ($LASTEXITCODE -ne 0) { throw 'Invalid PAPER schedule; no stale batch fallback.' }
    return ($paperScheduleJson | ConvertFrom-Json)
}
