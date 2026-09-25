# The coordinator is PowerShell, not the writer model: every role below gets a
# separate ephemeral Codex process and the verifier cannot see writer reasoning.
param([Parameter(Mandatory = $true)][string]$Target, [switch]$DryRun)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
if ($Target -match '^posting:(\d+)$') {
    $initArgs = @('init', '--posting', $Matches[1])
} elseif ($Target -match '^\d+$') {
    $initArgs = @('init', '--application', $Target)
} else {
    throw 'Target must be an application ID or posting:<id>.'
}
if ($DryRun) {
    Write-Output "Would initialise $Target, then run separate drafter, verifier, style critic and red-team sessions for up to three rounds."
    return
}

# A dirty application folder is owned by an active driver. Do not rebuild its
# brief or overwrite its drafts. A posting target is refused while any app
# folder is dirty, since its eventual folder cannot be known before init.
$dirty = @(& git status --porcelain=v1 --untracked-files=all)
if ($Target -match '^posting:' -and @($dirty | Where-Object { $_ -match 'brain/vault/applications/' }).Count -gt 0) {
    throw 'Another driver has uncommitted application files; use an application ID after that driver finishes.'
}
if ($Target -match '^\d+$') {
    $padded = $Target.PadLeft(4, '0')
    if (@($dirty | Where-Object { $_ -match "brain/vault/applications/$padded-" }).Count -gt 0) {
        throw "Application $Target has uncommitted files; do not overwrite another driver's work."
    }
}

$initOutput = @(& python -m pipeline.drafts @initArgs)
if ($LASTEXITCODE -ne 0) {
    $initOutput | Write-Output
    throw "Application init refused (exit $LASTEXITCODE)."
}
$initOutput | Write-Output
$folderLine = $initOutput | Where-Object { $_ -like 'folder: *' } | Select-Object -Last 1
$briefLine = $initOutput | Where-Object { $_ -like 'brief: *' } | Select-Object -Last 1
if (-not $folderLine -or -not $briefLine) { throw 'Application init did not report its folder and brief.' }
$folder = $folderLine.Substring(8).Trim()
$brief = $briefLine.Substring(7).Trim()
$briefText = Get-Content -LiteralPath $brief -Raw -Encoding utf8
if ($briefText -notmatch '(?m)^application_id:\s*(\d+)\s*$') { throw 'Brief has no application_id.' }
$applicationId = $Matches[1]
$questionsPath = Join-Path $folder 'questions.yaml'
$expectedAnswers = 0
if (Test-Path -LiteralPath $questionsPath) {
    $expectedAnswers = @((Get-Content -LiteralPath $questionsPath) | Where-Object { $_ -match '^\s*-\s+question:' }).Count
}
if ($briefText -match '(?m)^company_note:\s*(?:null|~)\s*$') {
    if ($briefText -notmatch '(?m)^company:\s*(.+)\s*$') { throw 'Brief has no company name.' }
    $company = $Matches[1].Trim()
    & (Join-Path $PSScriptRoot 'codex-research.ps1') -Company $company
    if ($LASTEXITCODE -ne 0) { throw 'Company research did not complete.' }
    & python -m pipeline.drafts init --application $applicationId
    if ($LASTEXITCODE -ne 0) { throw 'Could not rebuild brief after company research.' }
}

$passed = $false
$latestDrafts = @()
foreach ($round in 1..3) {
    $existing = @(Get-ChildItem -LiteralPath (Join-Path $folder 'drafts') -Filter "*-r$round.md" -File -ErrorAction SilentlyContinue)
    if ($existing.Count -gt 0) { throw "Round $round drafts already exist; refusing to overwrite them." }
    & (Join-Path $PSScriptRoot '..\codex\invoke-agent.ps1') -Role drafter -Target $folder -Round $round -Effort xhigh
    if ($LASTEXITCODE -ne 0) { throw "Drafter round $round failed." }
    $roundDrafts = @(Get-ChildItem -LiteralPath (Join-Path $folder 'drafts') -Filter "*-r$round.md" -File -ErrorAction SilentlyContinue)
    if ($roundDrafts.Count -eq 0) { throw "Drafter round $round produced no drafts." }
    $roundPassed = $true
    foreach ($draft in $roundDrafts) {
        & (Join-Path $PSScriptRoot 'codex-verify.ps1') -Draft $draft.FullName
        & (Join-Path $PSScriptRoot 'codex-style.ps1') -Draft $draft.FullName
        & (Join-Path $PSScriptRoot 'codex-red-team.ps1') -Draft $draft.FullName
        $reportDir = Join-Path $folder 'reports'
        $deterministic = Join-Path $reportDir "verify-$($draft.BaseName).json"
        if (-not (Test-Path -LiteralPath $deterministic)) { throw "Missing deterministic report for $($draft.Name)." }
        $hardFails = @((Get-Content -LiteralPath $deterministic -Raw | ConvertFrom-Json).hard_fails).Count
        if ($hardFails -gt 0) { $roundPassed = $false }
        Write-Output "$($draft.Name): deterministic hard failures=$hardFails"
    }
    # Round 2/3 may repair only failed answers. Assess the latest draft of
    # every kind, including still-valid prior-round files, before registering.
    $allDrafts = @(Get-ChildItem -LiteralPath (Join-Path $folder 'drafts') -Filter '*-r*.md' -File)
    $latestDrafts = @($allDrafts | Group-Object { $_.BaseName -replace '-r\d+$', '' } | ForEach-Object {
        $_.Group | Sort-Object { [int]([regex]::Match($_.BaseName, '-r(\d+)$').Groups[1].Value) } -Descending | Select-Object -First 1
    })
    $answerDrafts = @($latestDrafts | Where-Object { $_.BaseName -match '^answer-\d+-r\d+$' })
    if ($answerDrafts.Count -lt $expectedAnswers) {
        Write-Output "Only $($answerDrafts.Count) of $expectedAnswers question answers exist; revision required."
        $roundPassed = $false
    }
    foreach ($draft in $latestDrafts) {
        $reportDir = Join-Path $folder 'reports'
        $deterministic = Join-Path $reportDir "verify-$($draft.BaseName).json"
        if (-not (Test-Path -LiteralPath $deterministic)) { $roundPassed = $false; continue }
        if ((Get-Item -LiteralPath $deterministic).LastWriteTimeUtc -lt $draft.LastWriteTimeUtc) {
            Write-Output "Stale deterministic report for $($draft.Name); revision required."
            $roundPassed = $false
        }
        if (@((Get-Content -LiteralPath $deterministic -Raw | ConvertFrom-Json).hard_fails).Count -gt 0) { $roundPassed = $false }
        foreach ($check in @(
            @{ Name = 'verifier'; Expected = 'VERDICT: PASS' },
            @{ Name = 'style-critic'; Expected = 'STYLE: PASS' },
            @{ Name = 'red-team'; Expected = 'RED TEAM: PASS' }
        )) {
            $path = Join-Path $reportDir "codex-$($check.Name)-$($draft.BaseName)-gpt-6-sol.md"
            if (-not (Test-Path -LiteralPath $path)) { $roundPassed = $false; continue }
            if ((Get-Item -LiteralPath $path).LastWriteTimeUtc -lt $draft.LastWriteTimeUtc) {
                Write-Output "Stale $($check.Name) report for $($draft.Name); revision required."
                $roundPassed = $false
            }
            if ((Get-Content -LiteralPath $path -TotalCount 1).Trim() -ne $check.Expected) { $roundPassed = $false }
        }
    }
    if ($roundPassed) { $passed = $true; break }
}
if (-not $passed) {
    Write-Output 'No latest round passed every check. Drafts remain unregistered for the candidate to inspect.'
    exit 3
}

foreach ($draft in $latestDrafts) {
    & python -m pipeline.drafts register $applicationId $draft.FullName
    if ($LASTEXITCODE -ne 0) { throw "Could not register $($draft.Name)." }
}
& python -m pipeline.drafts ready $applicationId
if ($LASTEXITCODE -ne 0) { throw "Application $applicationId could not enter the candidate's review queue." }
Write-Output "Application $applicationId awaits the candidate's review and independent Claude-family review; nothing was submitted."
