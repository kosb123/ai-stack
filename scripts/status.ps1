# One-page status of the local AI stack. Read-only: it starts and stops nothing.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\status.ps1
# Exit code is 1 when a service that should always run is down.
$ErrorActionPreference = 'SilentlyContinue'
$hermesHome = 'C:\Users\jun\AppData\Local\hermes'
$llamaKeyFile = 'C:\Users\jun\AppData\Local\Hermes\runtimes\llamacpp\.api_key'

function Test-Http([string]$url) {
    try { return (Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 5).StatusCode -eq 200 } catch { return $false }
}

function Get-MB($pids) {
    $bytes = (Get-Process -Id $pids | Measure-Object WorkingSet64 -Sum).Sum
    return [int]($bytes / 1MB)
}

function Get-PortOwners([int]$port) {
    return @(Get-NetTCPConnection -State Listen -LocalPort $port | Select-Object -ExpandProperty OwningProcess -Unique)
}

function Get-Exposure([int[]]$ports) {
    $open = Get-NetTCPConnection -State Listen | Where-Object { $ports -contains $_.LocalPort -and $_.LocalAddress -in '0.0.0.0', '::' }
    if ($open) { return "EXPOSED on all interfaces" }
    return ''
}

function Get-Autostart([string]$task) {
    $found = Get-ScheduledTask -TaskName $task
    if (-not $found) { return 'NONE' }
    if ($found.State -eq 'Disabled') { return 'DISABLED' }
    return 'at logon'
}

$rows = New-Object System.Collections.ArrayList
function Add-Row($name, $state, $port, $mb, $autostart, $note) {
    [void]$rows.Add([pscustomobject]@{ Service = $name; State = $state; Port = $port; RamMB = $mb; Autostart = $autostart; Note = $note })
}

# Hermes gateway: gateway.pid names the live process.
$gatewayPid = $null
try { $gatewayPid = (Get-Content (Join-Path $hermesHome 'gateway.pid') -Raw | ConvertFrom-Json).pid } catch { }
$gatewayUp = $gatewayPid -and (Get-Process -Id $gatewayPid)
Add-Row 'Hermes gateway' $(if ($gatewayUp) { 'ok' } else { 'DOWN' }) '-' $(if ($gatewayUp) { Get-MB $gatewayPid } else { 0 }) 'app' ''

Add-Row 'OmniRoute' $(if (Test-Http 'http://127.0.0.1:20128/api/health') { 'ok' } else { 'DOWN' }) 20128 (Get-MB (Get-PortOwners 20128)) (Get-Autostart 'OmniRouteServer') (Get-Exposure @(20128))
Add-Row 'FreeLLMAPI' $(if (Test-Http 'http://127.0.0.1:3001/health') { 'ok' } else { 'DOWN' }) 3001 (Get-MB (Get-PortOwners 3001)) (Get-Autostart 'FreeLLMAPI') (Get-Exposure @(3001))

# llama-server sleeps on idle by design: the process stays up and the model leaves VRAM.
$llamaState = 'DOWN'
$llamaNote = ''
if (Test-Http 'http://127.0.0.1:18434/health') {
    $llamaState = 'ok'
    $llamaNote = 'model loaded'
    try {
        $key = (Get-Content $llamaKeyFile -Raw).Trim()
        $props = Invoke-RestMethod -Uri 'http://127.0.0.1:18434/props' -Headers @{ Authorization = "Bearer $key" } -TimeoutSec 5
        if ($props.is_sleeping) { $llamaState = 'sleeping'; $llamaNote = 'wakes on next request (20-40s)' }
    } catch { $llamaNote = 'state unknown' }
}
Add-Row 'llama-server' $llamaState 18434 (Get-MB (Get-PortOwners 18434)) (Get-Autostart 'Local LlamaCpp Server (Qwen3.8 27B)') $llamaNote

# Hindsight: the API process plus its embedded PostgreSQL.
$hindsightPids = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'hindsight\\run_api\.py' } | Select-Object -ExpandProperty ProcessId)
$hindsightPids += @(Get-Process postgres | Select-Object -ExpandProperty Id)
$hindsightUp = $false
try { $hindsightUp = (Invoke-RestMethod -Uri 'http://127.0.0.1:8888/health' -TimeoutSec 5).status -eq 'healthy' } catch { }
Add-Row 'Hindsight' $(if ($hindsightUp) { 'ok' } else { 'DOWN' }) '8888, 5432' $(if ($hindsightPids) { Get-MB $hindsightPids } else { 0 }) (Get-Autostart 'Hindsight API') (Get-Exposure @(8888, 5432))

# Paperclip runs in Docker and is started by hand, so stopped is a normal state.
$paperclipUp = (& docker inspect -f '{{.State.Running}}' docker-server-1 2>$null) -eq 'true'
$paperclipState = 'stopped'
if ($paperclipUp) { $paperclipState = if (Test-Http 'http://127.0.0.1:3100/api/health') { 'ok' } else { 'DOWN' } }
$vm = Get-Process vmmemWSL
Add-Row 'Paperclip (Docker)' $paperclipState '3100, 5433' $(if ($vm) { Get-MB $vm.Id } else { 0 }) 'manual' (Get-Exposure @(3100, 5433))

$rows | Format-Table -AutoSize | Out-String -Width 120 | ForEach-Object { $_.TrimEnd() }

$os = Get-CimInstance Win32_OperatingSystem
$vram = (& nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>$null)
'RAM free {0:N1} / {1:N1} GB | commit free {2:N1} GB | VRAM {3}' -f ($os.FreePhysicalMemory / 1MB), ($os.TotalVisibleMemorySize / 1MB), ($os.FreeVirtualMemory / 1MB), $vram

if ($rows | Where-Object { $_.State -eq 'DOWN' }) { exit 1 }
