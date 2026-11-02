# 2026-11-01: restart the local llama-server if it died (a failed wake from sleep exits the process).
# Starts it only when Windows has enough free commit: the server commits ~29-33 GB (its VRAM counts against commit
# on WDDM), and starting it short of that fails anyway and pushes every other app into "low virtual memory".
$ErrorActionPreference = 'Stop'
$runtimeRoot = 'C:\Users\jun\AppData\Local\Hermes\runtimes\llamacpp'
$log = Join-Path $runtimeRoot 'watchdog.log'
$minFreeCommitGB = 34

$running = Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" |
    Where-Object { $_.CommandLine -match '--port\s+18434(?:\s|$)' }
if ($running) {
    exit 0
}

$freeGB = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreeVirtualMemory / 1MB, 1)
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
if ($freeGB -lt $minFreeCommitGB) {
    Add-Content -Path $log -Value "$stamp not running; free commit $freeGB GB < $minFreeCommitGB GB, not starting"
    exit 0
}
Add-Content -Path $log -Value "$stamp not running; free commit $freeGB GB, starting"
& (Join-Path $runtimeRoot 'Start-External-LlamaCpp.ps1')
