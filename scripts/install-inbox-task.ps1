[CmdletBinding()]
param(
    [string]$TaskName = 'CareerOS Gmail Poller'
)

$ErrorActionPreference = 'Stop'
$Runner = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'run-inbox-poller.ps1')).Path
$PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
$Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Runner`""

$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes 3) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$Principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description 'CareerOS read-only Gmail delta poller; Calendar writes require an explicit separate command.' `
    -Force | Out-Null

Write-Output "Registered '$TaskName' to run every 3 minutes while this user is logged on."
