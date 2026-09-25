[CmdletBinding()]
param(
    [string]$TaskName = 'CareerOS Job Scan Fallback',
    [datetime]$At = (Get-Date '07:30')
)

$ErrorActionPreference = 'Stop'
$Runner = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'run-job-scan.ps1')).Path
$PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
$Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Runner`""

$Action = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument $Arguments `
    -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$Trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -WeeksInterval 1 `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
    -At $At
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
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
    -Description 'CareerOS weekday discovery, scoring and top-ten digest fallback. Disable this when the Desktop routine is enabled.' `
    -Force | Out-Null

Write-Output "Registered '$TaskName' for weekdays at $($At.ToString('HH:mm'))."
