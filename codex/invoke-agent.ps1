# One fresh Codex CLI session per role. No application text or credentials are
# placed on the command line: the prompt contains instructions and a path only.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('researcher', 'drafter', 'verifier', 'style-critic', 'red-team', 'coach', 'grounding-check', 'review', 'draft-workflow')]
    [string]$Role,
    [Parameter(Mandatory = $true)]
    [string]$Target,
    [ValidateSet('gpt-6-sol', 'gpt-6-luna')]
    [string]$Model = 'gpt-6-sol',
    [ValidateSet('low', 'medium', 'high', 'xhigh', 'max')]
    [string]$Effort = 'xhigh',
    [ValidateRange(1, 3)]
    [int]$Round = 1,
    [string]$OutputPath = '',
    [switch]$DryRun,
    [switch]$Overwrite
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$promptPath = Join-Path $PSScriptRoot "prompts\$Role.md"
if (-not (Test-Path -LiteralPath $promptPath -PathType Leaf)) {
    throw "No Codex prompt for role $Role"
}

$fileRoles = @('drafter', 'verifier', 'style-critic', 'red-team', 'coach', 'grounding-check')
$reportRoles = @('verifier', 'style-critic', 'red-team', 'grounding-check')
$resolvedTarget = $Target
if ($Role -in $fileRoles) {
    $candidate = if ([IO.Path]::IsPathRooted($Target)) { $Target } else { Join-Path $root $Target }
    $resolvedTarget = [IO.Path]::GetFullPath($candidate)
    $insideRoot = $resolvedTarget.StartsWith($root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)
    if (-not $insideRoot -or -not (Test-Path -LiteralPath $resolvedTarget)) {
        throw "Target must exist inside CareerOS: $Target"
    }
    if ($Role -in $reportRoles -and -not (Test-Path -LiteralPath $resolvedTarget -PathType Leaf)) {
        throw "Checker target must be a draft file: $Target"
    }
    if ($Role -in @('drafter', 'coach') -and -not (Test-Path -LiteralPath $resolvedTarget -PathType Container)) {
        throw "Writer target must be an application folder: $Target"
    }
}

$sandbox = if ($Role -in $reportRoles -or $Role -eq 'review') { 'read-only' } else { 'workspace-write' }
$reportPath = $null
if ($OutputPath -and $Role -notin $reportRoles) { throw '-OutputPath is only valid for read-only checker roles.' }
if ($Role -in $reportRoles) {
    $draftsDir = Split-Path -Parent $resolvedTarget
    if ((Split-Path -Leaf $draftsDir) -ne 'drafts') {
        throw "Checker target must be under an application's drafts/ folder: $Target"
    }
    $applicationDir = Split-Path -Parent $draftsDir
    $stem = [IO.Path]::GetFileNameWithoutExtension($resolvedTarget)
    $reportPath = if ($OutputPath) {
        $candidate = if ([IO.Path]::IsPathRooted($OutputPath)) { $OutputPath } else { Join-Path $root $OutputPath }
        [IO.Path]::GetFullPath($candidate)
    } else {
        Join-Path (Join-Path $applicationDir 'reports') "codex-$Role-$stem-$Model.md"
    }
    if (-not $reportPath.StartsWith($root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Report path must stay inside CareerOS.'
    }
    if ((Test-Path -LiteralPath $reportPath) -and -not $Overwrite) {
        throw "Report already exists; use -Overwrite to replace it: $reportPath"
    }
}

if ($DryRun) {
    Write-Output "role=$Role model=$Model effort=$Effort sandbox=$sandbox target=$resolvedTarget"
    if ($reportPath) { Write-Output "report=$reportPath" }
    return
}

if ($reportPath) {
    $reportDir = Split-Path -Parent $reportPath
    if (-not (Test-Path -LiteralPath $reportDir)) {
        New-Item -ItemType Directory -Path $reportDir | Out-Null
    }
}

$prompt = Get-Content -LiteralPath $promptPath -Raw -Encoding utf8
$prompt += "`n`nINPUT TARGET: $resolvedTarget`nROUND: $Round`n"
$args = @('exec', '-C', $root, '-m', $Model, '-c', "model_reasoning_effort=`"$Effort`"", '--ephemeral', '-s', $sandbox)
if ($reportPath) { $args += @('-o', $reportPath) }
$args += '-'
$prompt | & codex @args
if ($LASTEXITCODE -ne 0) { throw "Codex $Role failed with exit code $LASTEXITCODE" }
if ($reportPath -and (-not (Test-Path -LiteralPath $reportPath) -or (Get-Item -LiteralPath $reportPath).Length -eq 0)) {
    throw "Codex $Role returned no report: $reportPath"
}
