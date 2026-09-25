param([Parameter(Mandatory = $true)][string]$ApplicationFolder, [switch]$DryRun)
& (Join-Path $PSScriptRoot '..\codex\invoke-agent.ps1') -Role coach -Target $ApplicationFolder -Effort high -DryRun:$DryRun
