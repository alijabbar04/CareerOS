param([Parameter(Mandatory = $true)][string]$Company, [switch]$DryRun)
& (Join-Path $PSScriptRoot '..\codex\invoke-agent.ps1') -Role researcher -Target $Company -Effort high -DryRun:$DryRun
