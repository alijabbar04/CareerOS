param([Parameter(Mandatory = $true)][string]$Draft, [switch]$DryRun, [switch]$Overwrite)
if (-not $DryRun) {
    & python -m pipeline.verify $Draft
    # Exit 1 means the draft has hard failures; the independent verifier must
    # still inspect it and return a complete diff. Exit 2 is an invocation error.
    if ($LASTEXITCODE -gt 1) { throw "Deterministic verification failed to run: $Draft" }
}
& (Join-Path $PSScriptRoot '..\codex\invoke-agent.ps1') -Role verifier -Target $Draft -Effort xhigh -DryRun:$DryRun -Overwrite:$Overwrite
