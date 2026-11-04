# C:\Users\jun\hindsight\start-hindsight.ps1
# Idempotent local Hindsight API launcher. Safe to run repeatedly.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

# Guard: already up?
if (Get-NetTCPConnection -LocalPort 8888 -State Listen -ErrorAction SilentlyContinue) {
    Write-Output "hindsight already listening on 8888, exit"
    exit 0
}

# Load env (KEY=VALUE)
Get-Content (Join-Path $root 'hindsight.env') | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$') {
        Set-Item -Path ("Env:" + $matches[1]) -Value $matches[2]
    }
}

# HF model cache (bge-m3 embedder, MiniLM reranker) - extracted from old docker volume
$env:HF_HOME = Join-Path $root 'hf'
$env:HF_HUB_OFFLINE = "1"

# Redirected stdout uses cp949 here, and the startup banner crashes on it (hindsight #4974)
$env:PYTHONUTF8 = "1"

$py = Join-Path $root 'venv\Scripts\python.exe'
$runner = Join-Path $root 'run_api.py'
Start-Process -FilePath $py -ArgumentList "`"$runner`"" -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $root 'hindsight.out.log') `
    -RedirectStandardError  (Join-Path $root 'hindsight.err.log')
Write-Output "hindsight-api started"
