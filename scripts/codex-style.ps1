param([Parameter(Mandatory = $true)][string]$Draft, [ValidateSet('gpt-6-sol', 'gpt-6-luna')][string]$Model = 'gpt-6-sol', [switch]$DryRun, [switch]$Overwrite)
& (Join-Path $PSScriptRoot '..\codex\invoke-agent.ps1') -Role style-critic -Target $Draft -Model $Model -Effort high -DryRun:$DryRun -Overwrite:$Overwrite
