$ErrorActionPreference = 'Stop'

$runtimeRoot = 'C:\Users\jun\AppData\Local\Hermes\runtimes\llamacpp'
$serverExe = 'C:\Users\jun\AppData\Local\hermes\tools\llamacpp-cuda-11370-win32-x64\llama-server.exe'
$modelPath = 'C:\Users\jun\AppData\Local\hermes\models\Qwen3.8-27B-UD-Q4_K_M.gguf'
$mmprojPath = 'C:\Users\jun\AppData\Local\hermes\models\assets\mmproj-Qwen3.8-27B-BF16.gguf'
$keyFile = Join-Path $runtimeRoot '.api_key'
$stdoutLog = Join-Path $runtimeRoot 'external-server.stdout.log'
$stderrLog = Join-Path $runtimeRoot 'external-server.stderr.log'
$port = 18434

$existing = Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" |
    Where-Object { $_.CommandLine -match "--port\s+$port(?:\s|$)" } |
    Select-Object -First 1
if ($existing) {
    exit 0
}

foreach ($requiredPath in @($serverExe, $modelPath, $mmprojPath, $keyFile)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required local llama.cpp file is missing: $requiredPath"
    }
}

$serverArgs = @(
    '--host', '127.0.0.1',
    '--port', [string]$port,
    '--api-key-file', $keyFile,
    '--alias', 'Qwen3.8-27B-UD-Q4_K_M',
    '--model', $modelPath,
    '--mmproj', $mmprojPath,
    # 2026-10-31 tune: 2 slots sharing one 160K KV pool (auto = 4 slots, each holding its own
    # recurrent state). An explicit -np disables unified KV unless --kv-unified is passed.
    # Checkpoints (229 MiB each) and the host prompt cache are capped: the defaults
    # (32 per slot, 8 GiB) drove commit to 35 GB and crashed the server (bad allocation).
    '--ctx-size', '163840',
    '--parallel', '2',
    '--kv-unified',
    '--ubatch-size', '1024',
    '--ctx-checkpoints', '8',
    '--cache-ram', '2048',
    '--cache-type-k', 'q4_0',
    '--cache-type-v', 'q4_0',
    '--n-gpu-layers', '99',
    '--flash-attn', 'on',
    '--spec-type', 'draft-mtp',
    '--spec-draft-n-max', '2',
    '--backend-sampling',
    '--spec-draft-backend-sampling',
    '--top-k', '20',
    '--top-p', '0.95',
    '--temp', '1',
    '--min-p', '0.0',
    '--metrics',
    '--slots',
    '--ui',
    '--jinja',
    '--load-mode', 'dio',
    '--sleep-idle-seconds', '600'
)

# Passed via env: Windows PowerShell's Start-Process strips the quotes from a JSON argument.
$env:LLAMA_ARG_CHAT_TEMPLATE_KWARGS = '{"reasoning_effort":"medium"}'

# Keep the last run's log: a restart after a crash would otherwise overwrite the only record of why it died.
if (Test-Path -LiteralPath $stderrLog) {
    Move-Item -LiteralPath $stderrLog -Destination ($stderrLog -replace '\.log$', '.prev.log') -Force
}

Start-Process -FilePath $serverExe `
    -ArgumentList $serverArgs `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog
