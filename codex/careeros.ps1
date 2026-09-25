# Resume CareerOS work in Codex without writing a prompt.
#   .\codex\careeros.ps1            # pick the next task for Codex
#   .\codex\careeros.ps1 T-021      # take a specific task
#   .\codex\careeros.ps1 status     # report only
# Uses the model and reasoning effort from ~/.codex/config.toml (gpt-6-sol, xhigh).
param([string]$Focus = "")

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$prompt = Get-Content -Raw (Join-Path $PSScriptRoot "careeros.md")
if ($Focus) { $prompt += "`n`nFocus: $Focus" }
codex exec $prompt
