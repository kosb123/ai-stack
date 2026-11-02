# Daily Hindsight backup (local edition, post-Docker migration 2026-11-01):
# wait for the local API (which owns the embedded pg0 Postgres), take an online
# pg_dump of the database, run the health checks against the REST API,
# and keep the newest 7 archives.
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($env:PGPASSWORD)) { throw 'Set PGPASSWORD locally before running the backup.' }

$backupDir = 'C:\Users\jun\.hindsight\backups'
$logFile = Join-Path $backupDir 'backup.log'
$keep = 7
$installDir = 'C:\Users\jun\.pg0\installation\18.1.0'
$pgDump = Join-Path $installDir 'bin\pg_dump.exe'
$apiLogs = @('E:\hindsight\hindsight.out.log', 'E:\hindsight\hindsight.err.log')

New-Item -ItemType Directory -Force $backupDir | Out-Null
function Log($msg) { Add-Content -Path $logFile -Value ("{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg) }
function HealthOk() {
    try { return (Invoke-WebRequest 'http://localhost:8888/health' -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { return $false }
}
function ApiLogTail($lines) {
    foreach ($apiLog in $apiLogs) {
        if (Test-Path $apiLog) { Get-Content $apiLog -Tail $lines -ErrorAction SilentlyContinue }
    }
}

# The API starts at logon via the Startup VBS (E:\hindsight\start-hindsight.ps1), which itself
# waits for the embedded Postgres. Wait up to 10 min for the health endpoint.
$deadline = (Get-Date).AddMinutes(10)
while (-not (HealthOk)) {
    if ((Get-Date) -gt $deadline) { Log 'FAILED: Hindsight API not healthy 10 min after start, backup skipped'; exit 1 }
    Start-Sleep 15
}

$name = "hindsight-data-{0}.dump" -f (Get-Date -Format 'yyyyMMdd-HHmm')
Log "start $name"
$previousPassword = $env:PGPASSWORD
# PGPASSWORD is supplied by the caller.
try {
    $tmp = Join-Path $env:TEMP ("hindsight-dump-" + [guid]::NewGuid().ToString('N'))
    & $pgDump -h 127.0.0.1 -p 5432 -U hindsight -d hindsight --format=custom --compress=6 --no-owner --no-privileges --file="$tmp"
    if ($LASTEXITCODE -ne 0) { throw "pg_dump exited with $LASTEXITCODE" }
    Move-Item $tmp (Join-Path $backupDir $name) -Force
    Log ("ok {0} ({1:N1} MB)" -f $name, ((Get-Item (Join-Path $backupDir $name)).Length / 1MB))
} catch {
    Log "FAILED: $_"
    exit 1
} finally {
    $env:PGPASSWORD = $previousPassword
}

# Health checks (REST, same as before — the endpoints are identical in the local build).
try {
    $since = (Get-Date).ToUniversalTime().AddDays(-1)
    $resp = Invoke-RestMethod 'http://localhost:8888/v1/default/banks/jun/operations?limit=100'
    $ops = if ($resp.operations) { $resp.operations } else { $resp.items }
    # Ask for failed ops by status: the newest 100 ops are mostly retains, so a failure from earlier in the
    # day fell outside them (2026-10-30 reported "0 failed" while a page refresh had failed 16 h before).
    $respFailed = Invoke-RestMethod 'http://localhost:8888/v1/default/banks/jun/operations?status=failed&limit=100'
    $failedOps = if ($respFailed.operations) { $respFailed.operations } else { $respFailed.items }
    $failed = @($failedOps | Where-Object { ([datetime]$_.created_at).ToUniversalTime() -gt $since })
    $healthFile = Join-Path $backupDir 'health.log'
    if ($failed.Count -eq 0) {
        Log 'health ok: 0 failed jobs in 24h'
    } else {
        Log "health WARN: $($failed.Count) failed jobs in 24h (details in health.log)"
        foreach ($op in $failed) {
            Add-Content -Path $healthFile -Value ("{0} {1} {2} {3}" -f (Get-Date -Format 'yyyy-MM-dd'), $op.task_type, $op.document_id, $op.error_message)
        }
    }

    # Consolidation backlog: pages and recall go stale when facts pile up unconsolidated
    # (on 2026-10-22 page refreshes starved consolidation for 3 hours).
    $stats = Invoke-RestMethod 'http://localhost:8888/v1/default/banks/jun/stats'
    $lastConsolidated = if ($stats.last_consolidated_at) { ([datetime]$stats.last_consolidated_at).ToUniversalTime() } else { $null }
    if ($stats.pending_consolidation -gt 20 -or ($stats.pending_consolidation -gt 0 -and $lastConsolidated -and $lastConsolidated -lt (Get-Date).ToUniversalTime().AddHours(-6))) {
        Log "health WARN: consolidation backlog $($stats.pending_consolidation) facts, last consolidated $lastConsolidated UTC"
    } else {
        Log "health ok: consolidation backlog $($stats.pending_consolidation)"
    }

    # Orphaned or stuck work: anything still processing after 2 hours. (The docker-log worker check is
    # replaced by a scan of the local API stderr log; best-effort.)
    $stuck = @($ops | Where-Object { $_.status -eq 'processing' -and ([datetime]$_.updated_at).ToUniversalTime() -lt (Get-Date).ToUniversalTime().AddHours(-2) })
    $tail = ApiLogTail 5000
    $workerLine = @($tail | Select-String 'WORKER_STATS' | Select-Object -Last 1)
    $orphans = if ($workerLine -and "$workerLine" -match 'others: (\S[^|]*)') { $Matches[1].Trim() } else { 'unknown' }
    if ($stuck.Count -gt 0 -or ($orphans -ne 'none' -and $orphans -ne 'unknown')) {
        Log "health WARN: $($stuck.Count) jobs processing >2h, other workers holding tasks: $orphans"
    } else {
        Log "health ok: no stuck jobs, other workers: $orphans"
    }

    # Paused knowledge pages: a non-transient refresh failure stops a page's automatic refresh until a manual
    # refresh succeeds, and neither /health nor the API reports it (hindsight#6077, open). The hourly
    # "Hindsight Unpause Pages" task re-sends those refreshes; a page still listed here is failing repeatedly.
    $models = (Invoke-RestMethod 'http://localhost:8888/v1/default/banks/jun/mental-models?limit=100').items
    $paused = @($models | Where-Object { $_.last_refresh_failed_at -and (-not $_.last_refreshed_at -or [datetime]$_.last_refresh_failed_at -gt [datetime]$_.last_refreshed_at) })
    if ($paused.Count -gt 0) {
        Log "health WARN: $($paused.Count) knowledge pages paused after a failed refresh (details in health.log)"
        foreach ($m in $paused) {
            Add-Content -Path $healthFile -Value ("{0} paused page {1} {2} failed at {3}" -f (Get-Date -Format 'yyyy-MM-dd'), $m.id, $m.name, $m.last_refresh_failed_at)
        }
    } else {
        Log 'health ok: 0 paused knowledge pages'
    }

    # LLM call timeouts: retried calls never become failed jobs, so they only show in the log
    # (on 2026-10-22 consolidation silently retried 4x at the 300s per-call limit).
    $timeouts = @($tail | Select-String 'Connection error.*Timeout|TimeoutError')
    if ($timeouts.Count -gt 0) {
        Log "health WARN: $($timeouts.Count) LLM call timeouts in recent API log"
    } else {
        Log 'health ok: 0 LLM call timeouts in recent API log'
    }
} catch {
    Log "health check FAILED: $_"
    exit 1
}

Get-ChildItem $backupDir -Filter 'hindsight-data-*' | Where-Object { $_.Name -notlike '*.log' } | Sort-Object LastWriteTime -Descending | Select-Object -Skip $keep | ForEach-Object {
    Remove-Item $_.FullName
    Log "pruned $($_.Name)"
}
