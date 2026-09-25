<#
.SYNOPSIS
    T-016: browse the CareerOS database read-only with Datasette.

.DESCRIPTION
    Serves %USERPROFILE%\CareerOS-data\careeros.sqlite at http://127.0.0.1:8001 and opens
    it in the default browser. Uses --immutable, which is appropriate here because
    Datasette itself only ever reads: the live database stays in WAL mode (set by
    pipeline.db.connect()) so the real pipeline can keep writing to it concurrently.
    Stop the server with Ctrl+C.
#>

$ErrorActionPreference = "Stop"

$python = "C:\Users\<user>\AppData\Local\Programs\Python\Python313\python.exe"
$localDir = if ($env:CAREEROS_LOCAL_DIR) { $env:CAREEROS_LOCAL_DIR } else { Join-Path $env:USERPROFILE "CareerOS-data" }
$dbPath = Join-Path $localDir "careeros.sqlite"

if (-not (Test-Path $python)) {
    Write-Error "Python interpreter not found at $python"
    exit 1
}
if (-not (Test-Path $dbPath)) {
    Write-Error "Database not found at $dbPath. Run 'python -m pipeline.db migrate' first."
    exit 1
}

& $python -m datasette serve --immutable "$dbPath" --host 127.0.0.1 --port 8001 --open
