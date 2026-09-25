<#
.SYNOPSIS
    T-013: run the CareerOS Discord bot (app/discord-bot/bot.py).

.DESCRIPTION
    Long-running foreground process; stop with Ctrl+C. Requires .env (repo root) to
    hold DISCORD_BOT_TOKEN, DISCORD_APPROVALS_CHANNEL_ID and DISCORD_OWNER_ID -- see
    app/discord-bot/README.md for how to get each value. Not wired into any scheduled
    task; run it by hand whenever you want the bot listening.
#>

$ErrorActionPreference = "Stop"

$python = "C:\Users\<user>\AppData\Local\Programs\Python\Python313\python.exe"
$repoRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repoRoot ".env"
$bot = Join-Path $repoRoot "app\discord-bot\bot.py"

if (-not (Test-Path $python)) {
    Write-Error "Python interpreter not found at $python"
    exit 1
}
if (-not (Test-Path $envFile)) {
    Write-Error "$envFile not found. Copy .env.example to .env and fill in DISCORD_BOT_TOKEN, DISCORD_APPROVALS_CHANNEL_ID and DISCORD_OWNER_ID first -- see app\discord-bot\README.md."
    exit 1
}
if (-not (Test-Path $bot)) {
    Write-Error "Bot script not found at $bot"
    exit 1
}

Set-Location $repoRoot
& $python $bot
