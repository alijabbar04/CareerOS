param([string]$Application = 'queue', [switch]$DryRun)
& (Join-Path $PSScriptRoot '..\codex\invoke-agent.ps1') -Role review -Target $Application -Effort high -DryRun:$DryRun
