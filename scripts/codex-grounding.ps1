param([Parameter(Mandatory = $true)][string]$Draft, [switch]$DryRun, [switch]$Overwrite)
& (Join-Path $PSScriptRoot '..\codex\invoke-agent.ps1') -Role grounding-check -Target $Draft -Effort xhigh -DryRun:$DryRun -Overwrite:$Overwrite
