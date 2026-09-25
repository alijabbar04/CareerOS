[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$LocalRoot = if ($env:CAREEROS_LOCAL_DIR) {
    [System.IO.Path]::GetFullPath($env:CAREEROS_LOCAL_DIR)
} else {
    Join-Path $env:USERPROFILE 'CareerOS-data'
}
$PausedFlag = Join-Path $LocalRoot 'PAUSED'
if (Test-Path -LiteralPath $PausedFlag) {
    exit 0
}

$Python = 'C:\Users\<user>\AppData\Local\Programs\Python\Python313\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'CareerOS Python was not found at the configured path.'
}

$LogDir = Join-Path $LocalRoot 'logs'
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$LogPath = Join-Path $LogDir 'inbox-poller.log'

Push-Location -LiteralPath $RepoRoot
try {
    & $Python -m pipeline.inbox poll *>> $LogPath
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
