$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath 'C:\Users\Neila\quant-research'
try {
    if (Test-Path -LiteralPath 'config/private_paper.json') {
        throw 'Private configuration already exists. Review it locally; this helper will not overwrite it.'
    }
    Write-Host 'PAPER ONLY. Keep TWS Read-Only API checked during commissioning.'
    Write-Host 'Verify the exact DU account in the authenticated TWS PAPER account UI.'
    Write-Host 'Do not use an account discovered from the API or infer PAPER from its port.'
    $paperAccount = (Read-Host 'Independently verified DU paper account (saved locally only)').Trim()
    if ($paperAccount -cnotmatch '^DUT?[0-9]+$') { throw 'Only an exact DU/DUT paper account is supported.' }
    $paperPort = [int](Read-Host 'Actual socket port shown in TWS API Settings')
    if ($paperPort -lt 1 -or $paperPort -gt 65535) { throw 'Invalid socket port.' }
    $paperConfirmation = Read-Host 'Type VERIFIED PAPER to confirm the account was independently verified in the PAPER UI'
    if ($paperConfirmation -cne 'VERIFIED PAPER') { throw 'Independent PAPER verification is required.' }
    $paperConfig = [ordered]@{
        mode = 'PAPER'; execution_enabled = $false; host = '127.0.0.1'
        port = $paperPort; client_id = 92226; expected_account = $paperAccount
        paper_account_allowlist = @($paperAccount); independently_verified_paper = $true
    }
    $paperJson = $paperConfig | ConvertTo-Json -Depth 4
    $paperPath = Join-Path (Get-Location) 'config/private_paper.json'
    $paperStream = [IO.File]::Open($paperPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try {
        $paperBytes = [Text.UTF8Encoding]::new($false).GetBytes($paperJson)
        $paperStream.Write($paperBytes, 0, $paperBytes.Length)
        $paperStream.Flush($true)
    } finally { $paperStream.Dispose() }
    Write-Host 'Saved locally with execution disabled. No broker orders were sent.'
} catch {
    Write-Host ('CONFIGURATION FAILED: ' + $_.Exception.Message) -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 2
}
Read-Host 'Press Enter to close'

